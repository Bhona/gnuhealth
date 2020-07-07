# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.
import datetime
from itertools import chain
from decimal import Decimal

from sql import Literal, Null
from sql.aggregate import Count
from sql.operators import Concat

from trytond.model import Workflow, Model, ModelView, ModelSQL, fields, \
    sequence_ordered
from trytond.modules.company import CompanyReport
from trytond.wizard import Wizard, StateAction, StateView, StateTransition, \
    Button
from trytond import backend
from trytond.pyson import Eval, Bool, If, PYSONEncoder
from trytond.transaction import Transaction
from trytond.pool import Pool

from trytond.modules.account.tax import TaxableMixin
from trytond.modules.product import price_digits

__all__ = ['Purchase', 'PurchaseIgnoredInvoice',
    'PurchaseRecreadtedInvoice', 'PurchaseLine', 'PurchaseLineTax',
    'PurchaseLineIgnoredMove', 'PurchaseLineRecreatedMove', 'PurchaseReport',
    'OpenSupplier', 'HandleShipmentExceptionAsk', 'HandleShipmentException',
    'HandleInvoiceExceptionAsk', 'HandleInvoiceException', 'ModifyHeader',
    'get_shipments_returns', 'search_shipments_returns']

_STATES = {
    'readonly': Eval('state') != 'draft',
    }
_DEPENDS = ['state']
_ZERO = Decimal(0)
STATES = [
    ('draft', 'Draft'),
    ('quotation', 'Quotation'),
    ('confirmed', 'Confirmed'),
    ('processing', 'Processing'),
    ('done', 'Done'),
    ('cancel', 'Canceled'),
    ]


def get_shipments_returns(model_name):
    "Computes the returns or shipments"
    def method(self, name):
        Model = Pool().get(model_name)
        shipments = set()
        for line in self.lines:
            for move in line.moves:
                if isinstance(move.shipment, Model):
                    shipments.add(move.shipment.id)
        return list(shipments)
    return method


def search_shipments_returns(model_name):
    "Search on shipments or returns"
    def method(self, name, clause):
        nested = clause[0].lstrip(name)
        if nested:
            return [('lines.moves.shipment' + nested,)
                + tuple(clause[1:3]) + (model_name,) + tuple(clause[3:])]
        else:
            if isinstance(clause[2], str):
                target = 'rec_name'
            else:
                target = 'id'
            return [('lines.moves.shipment.' + target,)
                + tuple(clause[1:3]) + (model_name,)]
    return classmethod(method)


class Purchase(Workflow, ModelSQL, ModelView, TaxableMixin):
    'Purchase'
    __name__ = 'purchase.purchase'
    _rec_name = 'number'
    company = fields.Many2One('company.company', 'Company', required=True,
        states={
            'readonly': (Eval('state') != 'draft') | Eval('lines', [0]),
            },
        domain=[
            ('id', If(Eval('context', {}).contains('company'), '=', '!='),
                Eval('context', {}).get('company', -1)),
            ],
        depends=['state'], select=True)
    number = fields.Char('Number', size=None, readonly=True, select=True)
    reference = fields.Char('Reference', select=True)
    description = fields.Char('Description', size=None, states=_STATES,
        depends=_DEPENDS)
    state = fields.Selection(STATES, 'State', readonly=True, required=True)
    purchase_date = fields.Date('Purchase Date',
        states={
            'readonly': ~Eval('state').in_(['draft', 'quotation']),
            'required': ~Eval('state').in_(['draft', 'quotation', 'cancel']),
            },
        depends=['state'])
    payment_term = fields.Many2One('account.invoice.payment_term',
        'Payment Term', states={
            'readonly': ~Eval('state').in_(['draft', 'quotation']),
            },
        depends=['state'])
    party = fields.Many2One('party.party', 'Party', required=True,
        states={
            'readonly': ((Eval('state') != 'draft')
                | (Eval('lines', [0]) & Eval('party'))),
            },
        select=True, depends=['state'])
    party_lang = fields.Function(fields.Char('Party Language'),
        'on_change_with_party_lang')
    invoice_address = fields.Many2One('party.address', 'Invoice Address',
        domain=[('party', '=', Eval('party'))],
        states={
            'readonly': Eval('state') != 'draft',
            'required': ~Eval('state').in_(['draft', 'quotation', 'cancel']),
            },
        depends=['state', 'party'])
    warehouse = fields.Many2One('stock.location', 'Warehouse',
        domain=[('type', '=', 'warehouse')], states=_STATES,
        depends=_DEPENDS)
    currency = fields.Many2One('currency.currency', 'Currency', required=True,
        states={
            'readonly': ((Eval('state') != 'draft')
                | (Eval('lines', [0]) & Eval('currency'))),
            },
        depends=['state'])
    currency_digits = fields.Function(fields.Integer('Currency Digits'),
        'on_change_with_currency_digits')
    lines = fields.One2Many('purchase.line', 'purchase', 'Lines',
        states=_STATES, depends=_DEPENDS)
    comment = fields.Text('Comment')
    untaxed_amount = fields.Function(fields.Numeric('Untaxed',
            digits=(16, Eval('currency_digits', 2)),
            depends=['currency_digits']), 'get_amount')
    untaxed_amount_cache = fields.Numeric('Untaxed Cache',
        digits=(16, Eval('currency_digits', 2)),
        depends=['currency_digits'])
    tax_amount = fields.Function(fields.Numeric('Tax',
            digits=(16, Eval('currency_digits', 2)),
            depends=['currency_digits']), 'get_amount')
    tax_amount_cache = fields.Numeric('Tax Cache',
        digits=(16, Eval('currency_digits', 2)),
        depends=['currency_digits'])
    total_amount = fields.Function(fields.Numeric('Total',
            digits=(16, Eval('currency_digits', 2)),
            depends=['currency_digits']), 'get_amount')
    total_amount_cache = fields.Numeric('Total Cache',
        digits=(16, Eval('currency_digits', 2)),
        depends=['currency_digits'])
    invoice_method = fields.Selection([
            ('manual', 'Manual'),
            ('order', 'Based On Order'),
            ('shipment', 'Based On Shipment'),
            ], 'Invoice Method', required=True, states=_STATES,
        depends=_DEPENDS)
    invoice_state = fields.Selection([
            ('none', 'None'),
            ('waiting', 'Waiting'),
            ('paid', 'Paid'),
            ('exception', 'Exception'),
            ], 'Invoice State', readonly=True, required=True)
    invoices = fields.Function(fields.One2Many('account.invoice', None,
            'Invoices'), 'get_invoices', searcher='search_invoices')
    invoices_ignored = fields.Many2Many(
            'purchase.purchase-ignored-account.invoice',
            'purchase', 'invoice', 'Ignored Invoices', readonly=True)
    invoices_recreated = fields.Many2Many(
            'purchase.purchase-recreated-account.invoice',
            'purchase', 'invoice', 'Recreated Invoices', readonly=True)
    delivery_date = fields.Date(
        "Delivery Date",
        states={
            'readonly': Eval('state').in_([
                    'processing', 'done', 'cancel']),
            },
        depends=['state'],
        help="The default delivery date for each line.")
    shipment_state = fields.Selection([
            ('none', 'None'),
            ('waiting', 'Waiting'),
            ('received', 'Received'),
            ('exception', 'Exception'),
            ], 'Shipment State', readonly=True, required=True)
    shipments = fields.Function(fields.One2Many('stock.shipment.in', None,
            'Shipments'), 'get_shipments', searcher='search_shipments')
    shipment_returns = fields.Function(
        fields.One2Many('stock.shipment.in.return', None, 'Shipment Returns'),
        'get_shipment_returns', searcher='search_shipment_returns')
    moves = fields.One2Many('stock.move', 'purchase', 'Moves', readonly=True)

    @classmethod
    def __setup__(cls):
        super(Purchase, cls).__setup__()
        cls._order = [
            ('purchase_date', 'DESC'),
            ('id', 'DESC'),
            ]
        cls._error_messages.update({
                'warehouse_required': ('A warehouse must be defined for '
                    'quotation of purchase "%s".'),
                'delete_cancel': ('Purchase "%s" must be cancelled before '
                    'deletion.'),
                })
        cls._transitions |= set((
                ('draft', 'quotation'),
                ('quotation', 'confirmed'),
                ('confirmed', 'processing'),
                ('confirmed', 'draft'),
                ('processing', 'processing'),
                ('processing', 'done'),
                ('done', 'processing'),
                ('draft', 'cancel'),
                ('quotation', 'cancel'),
                ('quotation', 'draft'),
                ('cancel', 'draft'),
                ))
        cls._buttons.update({
                'cancel': {
                    'invisible': ~Eval('state').in_(['draft', 'quotation']),
                    'depends': ['state'],
                    },
                'draft': {
                    'invisible': ~Eval('state').in_(
                        ['cancel', 'quotation', 'confirmed']),
                    'icon': If(Eval('state') == 'cancel', 'tryton-undo',
                        'tryton-back'),
                    'depends': ['state'],
                    },
                'quote': {
                    'pre_validate': [
                        If(~Eval('purchase_date'),
                            ('purchase_date', '!=', None),
                            ()),
                        If(~Eval('invoice_address'),
                            ('invoice_address', '!=', None),
                            ()),
                        ],
                    'invisible': Eval('state') != 'draft',
                    'readonly': ~Eval('lines', []),
                    'depends': ['state'],
                    },
                'confirm': {
                    'invisible': Eval('state') != 'quotation',
                    'depends': ['state'],
                    },
                'process': {
                    'invisible': Eval('state') != 'confirmed',
                    'depends': ['state'],
                    },
                'handle_invoice_exception': {
                    'invisible': ((Eval('invoice_state') != 'exception')
                        | (Eval('state') == 'cancel')),
                    'depends': ['state', 'invoice_state'],
                    },
                'handle_shipment_exception': {
                    'invisible': ((Eval('shipment_state') != 'exception')
                        | (Eval('state') == 'cancel')),
                    'depends': ['state', 'shipment_state'],
                    },
                'modify_header': {
                    'invisible': ((Eval('state') != 'draft')
                        | ~Eval('lines', [-1])),
                    'depends': ['state'],
                    },
                })
        # The states where amounts are cached
        cls._states_cached = ['confirmed', 'done', 'cancel']

    @classmethod
    def __register__(cls, module_name):
        pool = Pool()
        PurchaseLine = pool.get('purchase.line')
        Move = pool.get('stock.move')
        InvoiceLine = pool.get('account.invoice.line')
        TableHandler = backend.get('TableHandler')
        cursor = Transaction().connection.cursor()
        sql_table = cls.__table__()

        table = cls.__table_handler__(module_name)

        # Migration from 3.8:
        if table.column_exist('supplier_reference'):
            table.column_rename('reference', 'number')
            table.column_rename('supplier_reference', 'reference')

        super(Purchase, cls).__register__(module_name)
        table = cls.__table_handler__(module_name)

        # Migration from 3.2
        # state confirmed splitted into confirmed and processing
        if (TableHandler.table_exist(PurchaseLine._table)
                and TableHandler.table_exist(Move._table)
                and TableHandler.table_exist(InvoiceLine._table)):
            purchase_line = PurchaseLine.__table__()
            move = Move.__table__()
            invoice_line = InvoiceLine.__table__()
            # Wrap subquery inside an other inner subquery because MySQL syntax
            # doesn't allow update a table and select from the same table in a
            # subquery.
            sub_query = sql_table.join(purchase_line,
                condition=purchase_line.purchase == sql_table.id
                ).join(invoice_line, 'LEFT',
                    condition=(invoice_line.origin ==
                        Concat(PurchaseLine.__name__ + ',', purchase_line.id))
                    ).join(move, 'LEFT',
                        condition=(move.origin == Concat(
                                PurchaseLine.__name__ + ',', purchase_line.id))
                        ).select(sql_table.id,
                            where=((sql_table.state == 'confirmed')
                                & ((invoice_line.id != Null)
                                    | (move.id != Null))))
            cursor.execute(*sql_table.update(
                    columns=[sql_table.state],
                    values=['processing'],
                    where=sql_table.id.in_(sub_query.select(sub_query.id))))

        # Add index on create_date
        table.index_action('create_date', action='add')

    @classmethod
    def default_payment_term(cls):
        PaymentTerm = Pool().get('account.invoice.payment_term')
        payment_terms = PaymentTerm.search(cls.payment_term.domain)
        if len(payment_terms) == 1:
            return payment_terms[0].id

    @classmethod
    def default_warehouse(cls):
        Location = Pool().get('stock.location')
        locations = Location.search(cls.warehouse.domain)
        if len(locations) == 1:
            return locations[0].id

    @staticmethod
    def default_company():
        return Transaction().context.get('company')

    @staticmethod
    def default_state():
        return 'draft'

    @staticmethod
    def default_currency():
        Company = Pool().get('company.company')
        company = Transaction().context.get('company')
        if company:
            company = Company(company)
            return company.currency.id

    @staticmethod
    def default_currency_digits():
        Company = Pool().get('company.company')
        company = Transaction().context.get('company')
        if company:
            company = Company(company)
            return company.currency.digits
        return 2

    @staticmethod
    def default_invoice_method():
        Configuration = Pool().get('purchase.configuration')
        configuration = Configuration(1)
        return configuration.purchase_invoice_method

    @staticmethod
    def default_invoice_state():
        return 'none'

    @staticmethod
    def default_shipment_state():
        return 'none'

    @fields.depends('party', 'payment_term', 'lines')
    def on_change_party(self):
        pool = Pool()
        Currency = pool.get('currency.currency')
        cursor = Transaction().connection.cursor()
        table = self.__table__()
        self.invoice_address = None
        self.payment_term = self.default_payment_term()
        if not self.lines:
            self.currency = self.default_currency()
            self.currency_digits = self.default_currency_digits()
        self.invoice_address = None
        if self.party:
            self.invoice_address = self.party.address_get(type='invoice')
            if self.party.supplier_payment_term:
                self.payment_term = self.party.supplier_payment_term

            if not self.lines:
                subquery = table.select(table.currency,
                    where=table.party == self.party.id,
                    order_by=table.id,
                    limit=10)
                cursor.execute(*subquery.select(subquery.currency,
                        group_by=subquery.currency,
                        order_by=Count(Literal(1)).desc))
                row = cursor.fetchone()
                if row:
                    currency_id, = row
                    self.currency = Currency(currency_id)
                    self.currency_digits = self.currency.digits

    @fields.depends('currency')
    def on_change_with_currency_digits(self, name=None):
        if self.currency:
            return self.currency.digits
        return 2

    @fields.depends('party')
    def on_change_with_party_lang(self, name=None):
        Config = Pool().get('ir.configuration')
        if self.party:
            if self.party.lang:
                return self.party.lang.code
        return Config.get_language()

    def _get_tax_context(self):
        context = {}
        if self.party and self.party.lang:
            context['language'] = self.party.lang.code
        return context

    @fields.depends('lines', 'currency', 'party')
    def on_change_lines(self):
        self.untaxed_amount = Decimal('0.0')
        self.tax_amount = Decimal('0.0')
        self.total_amount = Decimal('0.0')
        taxes = {}
        if self.lines:
            for line in self.lines:
                self.untaxed_amount += getattr(line, 'amount', None) or 0
            taxes = self._get_taxes()
            self.tax_amount = sum(t['amount'] for t in taxes.values())
        if self.currency:
            self.untaxed_amount = self.currency.round(self.untaxed_amount)
            self.tax_amount = self.currency.round(self.tax_amount)
        self.total_amount = self.untaxed_amount + self.tax_amount
        if self.currency:
            self.total_amount = self.currency.round(self.total_amount)

    @property
    def taxable_lines(self):
        taxable_lines = []
        # In case we're called from an on_change we have to use some sensible
        # defaults
        for line in self.lines:
            if getattr(line, 'type', None) != 'line':
                continue
            taxable_lines.append(tuple())
            for attribute, default_value in (
                    ('taxes', []),
                    ('unit_price', Decimal(0)),
                    ('quantity', 0.0)):
                value = getattr(line, attribute, None)
                taxable_lines[-1] += (value
                    if value is not None else default_value,)
        return taxable_lines

    def get_tax_amount(self):
        taxes = iter(self._get_taxes().values())
        return sum((tax['amount'] for tax in taxes), Decimal(0))

    @classmethod
    def get_amount(cls, purchases, names):
        untaxed_amount = {}
        tax_amount = {}
        total_amount = {}

        if {'tax_amount', 'total_amount'} & set(names):
            compute_taxes = True
        else:
            compute_taxes = False
        # Sort cached first and re-instanciate to optimize cache management
        purchases = sorted(purchases,
            key=lambda p: p.state in cls._states_cached, reverse=True)
        purchases = cls.browse(purchases)
        for purchase in purchases:
            if (purchase.state in cls._states_cached
                    and purchase.untaxed_amount_cache is not None
                    and purchase.tax_amount_cache is not None
                    and purchase.total_amount_cache is not None):
                untaxed_amount[purchase.id] = purchase.untaxed_amount_cache
                if compute_taxes:
                    tax_amount[purchase.id] = purchase.tax_amount_cache
                    total_amount[purchase.id] = purchase.total_amount_cache
            else:
                untaxed_amount[purchase.id] = sum(
                    (line.amount for line in purchase.lines
                        if line.type == 'line'), _ZERO)
                if compute_taxes:
                    tax_amount[purchase.id] = purchase.get_tax_amount()
                    total_amount[purchase.id] = (
                        untaxed_amount[purchase.id] + tax_amount[purchase.id])

        result = {
            'untaxed_amount': untaxed_amount,
            'tax_amount': tax_amount,
            'total_amount': total_amount,
            }
        for key in list(result.keys()):
            if key not in names:
                del result[key]
        return result

    def get_invoices(self, name):
        invoices = set()
        for line in self.lines:
            for invoice_line in line.invoice_lines:
                if invoice_line.invoice:
                    invoices.add(invoice_line.invoice.id)
        return list(invoices)

    @classmethod
    def search_invoices(cls, name, clause):
        return [('lines.invoice_lines.invoice' + clause[0].lstrip(name),)
            + tuple(clause[1:])]

    def get_invoice_state(self):
        '''
        Return the invoice state for the purchase.
        '''
        skip_ids = set(x.id for x in self.invoices_ignored)
        skip_ids.update(x.id for x in self.invoices_recreated)
        invoices = [i for i in self.invoices if i.id not in skip_ids]
        if invoices:
            if any(i.state == 'cancel' for i in invoices):
                return 'exception'
            elif all(i.state == 'paid' for i in invoices):
                return 'paid'
            else:
                return 'waiting'
        return 'none'

    def set_invoice_state(self):
        '''
        Set the invoice state.
        '''
        state = self.get_invoice_state()
        if self.invoice_state != state:
            self.write([self], {
                    'invoice_state': state,
                    })

    get_shipments = get_shipments_returns('stock.shipment.in')
    get_shipment_returns = get_shipments_returns('stock.shipment.in.return')

    search_shipments = search_shipments_returns('stock.shipment.in')
    search_shipment_returns = search_shipments_returns(
        'stock.shipment.in.return')

    def get_shipment_state(self):
        '''
        Return the shipment state for the purchase.
        '''
        if self.moves:
            if any(l.move_exception for l in self.lines):
                return 'exception'
            elif all(l.move_done for l in self.lines):
                return 'received'
            else:
                return 'waiting'
        return 'none'

    def set_shipment_state(self):
        '''
        Set the shipment state.
        '''
        state = self.get_shipment_state()
        if self.shipment_state != state:
            self.write([self], {
                    'shipment_state': state,
                    })

    @property
    def report_address(self):
        if self.invoice_address:
            return self.invoice_address.full_address
        else:
            return ''

    @property
    def delivery_full_address(self):
        if self.warehouse and self.warehouse.address:
            return self.warehouse.address.full_address
        return ''

    def get_rec_name(self, name):
        items = []
        if self.number:
            items.append(self.number)
        if self.reference:
            items.append('[%s]' % self.reference)
        if not items:
            items.append('(%s)' % self.id)
        return ' '.join(items)

    @classmethod
    def search_rec_name(cls, name, clause):
        _, operator, value = clause
        if operator.startswith('!') or operator.startswith('not '):
            bool_op = 'AND'
        else:
            bool_op = 'OR'
        domain = [bool_op,
            ('number', operator, value),
            ('reference', operator, value),
            ]
        return domain

    @classmethod
    def view_attributes(cls):
        attributes = [
            ('/form//field[@name="comment"]', 'spell', Eval('party_lang'))]
        if Transaction().context.get('modify_header'):
            attributes.extend([
                    ('//group[@id="states"]', 'states', {'invisible': True}),
                    ('//group[@id="amount_buttons"]',
                        'states', {'invisible': True}),
                    ('//page[@name="invoices"]',
                        'states', {'invisible': True}),
                    ('//page[@name="shipments"]',
                        'states', {'invisible': True}),
                    ])
        return attributes

    @classmethod
    def copy(cls, purchases, default=None):
        if default is None:
            default = {}
        else:
            default = default.copy()
        default.setdefault('number', None)
        default.setdefault('invoice_state', 'none')
        default.setdefault('invoices_ignored', None)
        default.setdefault('moves', None)
        default.setdefault('shipment_state', 'none')
        default.setdefault('purchase_date', None)
        return super(Purchase, cls).copy(purchases, default=default)

    def check_for_quotation(self):
        for line in self.lines:
            if (not line.to_location
                    and line.product
                    and line.product.type in ('goods', 'assets')):
                self.raise_user_error('warehouse_required', (self.rec_name,))

    @classmethod
    def set_number(cls, purchases):
        '''
        Fill the number field with the purchase sequence
        '''
        pool = Pool()
        Sequence = pool.get('ir.sequence')
        Config = pool.get('purchase.configuration')

        config = Config(1)
        for purchase in purchases:
            if purchase.number:
                continue
            purchase.number = Sequence.get_id(config.purchase_sequence.id)
        cls.save(purchases)

    @classmethod
    def set_purchase_date(cls, purchases):
        Date = Pool().get('ir.date')
        for purchase in purchases:
            if not purchase.purchase_date:
                cls.write([purchase], {
                        'purchase_date': Date.today(),
                        })

    @classmethod
    def store_cache(cls, purchases):
        for purchase in purchases:
            purchase.untaxed_amount_cache = purchase.untaxed_amount
            purchase.tax_amount_cache = purchase.tax_amount
            purchase.total_amount_cache = purchase.total_amount
        cls.save(purchases)

    def _get_invoice_purchase(self):
        'Return invoice'
        pool = Pool()
        Journal = pool.get('account.journal')
        Invoice = pool.get('account.invoice')

        journals = Journal.search([
                ('type', '=', 'expense'),
                ], limit=1)
        if journals:
            journal, = journals
        else:
            journal = None

        return Invoice(
            company=self.company,
            type='in',
            journal=journal,
            party=self.party,
            invoice_address=self.invoice_address,
            currency=self.currency,
            account=self.party.account_payable_used,
            payment_term=self.payment_term,
            )

    def create_invoice(self):
        'Create an invoice for the purchase and return it'
        pool = Pool()
        Invoice = pool.get('account.invoice')

        if self.invoice_method == 'manual':
            return

        invoice_lines = []
        for line in self.lines:
            invoice_lines.append(line.get_invoice_line())
        invoice_lines = list(chain(*invoice_lines))
        if not invoice_lines:
            return

        invoice = self._get_invoice_purchase()
        if getattr(invoice, 'lines', None):
            invoice_lines = list(invoice.lines) + invoice_lines
        invoice.lines = invoice_lines
        invoice.save()

        Invoice.update_taxes([invoice])
        return invoice

    def create_move(self, move_type):
        '''
        Create move for each purchase lines
        '''
        pool = Pool()
        Move = pool.get('stock.move')

        moves = []
        for line in self.lines:
            move = line.get_move(move_type)
            if move:
                moves.append(move)
        Move.save(moves)
        return moves

    @property
    def return_from_location(self):
        if self.warehouse:
            return (self.warehouse.supplier_return_location
                or self.warehouse.storage_location)

    def _get_return_shipment(self):
        ShipmentInReturn = Pool().get('stock.shipment.in.return')
        return ShipmentInReturn(
            company=self.company,
            from_location=self.return_from_location,
            to_location=self.party.supplier_location,
            supplier=self.party,
            delivery_address=self.party.address_get(type='delivery'),
            )

    def create_return_shipment(self, return_moves):
        '''
        Create return shipment and return the shipment id
        '''
        ShipmentInReturn = Pool().get('stock.shipment.in.return')
        return_shipment = self._get_return_shipment()
        return_shipment.moves = return_moves
        return_shipment.save()
        ShipmentInReturn.wait([return_shipment])
        return return_shipment

    def is_done(self):
        return ((self.invoice_state == 'paid'
                or self.invoice_state == 'none')
            and (self.shipment_state == 'received'
                or self.shipment_state == 'none'
                or all(l.product.type == 'service'
                    for l in self.lines if l.product)))

    @classmethod
    def delete(cls, purchases):
        # Cancel before delete
        cls.cancel(purchases)
        for purchase in purchases:
            if purchase.state != 'cancel':
                cls.raise_user_error('delete_cancel', purchase.rec_name)
        super(Purchase, cls).delete(purchases)

    @classmethod
    @ModelView.button
    @Workflow.transition('cancel')
    def cancel(cls, purchases):
        cls.store_cache(purchases)

    @classmethod
    @ModelView.button
    @Workflow.transition('draft')
    def draft(cls, purchases):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('quotation')
    def quote(cls, purchases):
        for purchase in purchases:
            purchase.check_for_quotation()
        cls.set_number(purchases)

    @classmethod
    @ModelView.button
    @Workflow.transition('confirmed')
    def confirm(cls, purchases):
        pool = Pool()
        Configuration = pool.get('purchase.configuration')
        cls.set_purchase_date(purchases)
        cls.store_cache(purchases)
        config = Configuration(1)
        with Transaction().set_context(
                queue_name='purchase',
                queue_scheduled_at=config.purchase_process_after):
            cls.__queue__.process(purchases)

    @classmethod
    @Workflow.transition('processing')
    def proceed(cls, purchases):
        pass

    @classmethod
    @Workflow.transition('done')
    def do(cls, purchases):
        pass

    @classmethod
    @ModelView.button_action('purchase.wizard_invoice_handle_exception')
    def handle_invoice_exception(cls, purchases):
        pass

    @classmethod
    @ModelView.button_action('purchase.wizard_shipment_handle_exception')
    def handle_shipment_exception(cls, purchases):
        pass

    @classmethod
    @ModelView.button
    def process(cls, purchases):
        process, done = [], []
        cls.__lock(purchases)
        for purchase in purchases:
            purchase.create_invoice()
            purchase.set_invoice_state()
            purchase.create_move('in')
            return_moves = purchase.create_move('return')
            if return_moves:
                purchase.create_return_shipment(return_moves)
            purchase.set_shipment_state()
            if purchase.is_done():
                if purchase.state != 'done':
                    if purchase.state == 'confirmed':
                        process.append(purchase)
                    done.append(purchase)
            elif purchase.state != 'processing':
                process.append(purchase)
        if process:
            cls.proceed(process)
        if done:
            cls.do(done)

    @classmethod
    def __lock(cls, records):
        from trytond.tools import grouped_slice, reduce_ids
        from sql import Literal, For
        transaction = Transaction()
        database = transaction.database
        connection = transaction.connection
        table = cls.__table__()

        if database.has_select_for():
            for sub_records in grouped_slice(records):
                where = reduce_ids(table.id, sub_records)
                query = table.select(
                    Literal(1), where=where, for_=For('UPDATE', nowait=True))
                with connection.cursor() as cursor:
                    cursor.execute(*query)
        else:
            database.lock(connection, cls._table)

    @classmethod
    @ModelView.button_action('purchase.wizard_modify_header')
    def modify_header(cls, purchases):
        pass


class PurchaseIgnoredInvoice(ModelSQL):
    'Purchase - Ignored Invoice'
    __name__ = 'purchase.purchase-ignored-account.invoice'
    _table = 'purchase_invoice_ignored_rel'
    purchase = fields.Many2One('purchase.purchase', 'Purchase',
            ondelete='CASCADE', select=True, required=True)
    invoice = fields.Many2One('account.invoice', 'Invoice',
            ondelete='RESTRICT', select=True, required=True)


class PurchaseRecreadtedInvoice(ModelSQL):
    'Purchase - Recreated Invoice'
    __name__ = 'purchase.purchase-recreated-account.invoice'
    _table = 'purchase_invoice_recreated_rel'
    purchase = fields.Many2One('purchase.purchase', 'Purchase',
            ondelete='CASCADE', select=True, required=True)
    invoice = fields.Many2One('account.invoice', 'Invoice',
            ondelete='RESTRICT', select=True, required=True)


class PurchaseLine(sequence_ordered(), ModelSQL, ModelView):
    'Purchase Line'
    __name__ = 'purchase.line'
    purchase = fields.Many2One('purchase.purchase', 'Purchase',
        ondelete='CASCADE', select=True, required=True,
        states={
            'readonly': ((Eval('purchase_state') != 'draft')
                & Bool(Eval('purchase'))),
            },
        depends=['purchase_state'])
    type = fields.Selection([
        ('line', 'Line'),
        ('subtotal', 'Subtotal'),
        ('title', 'Title'),
        ('comment', 'Comment'),
        ], 'Type', select=True, required=True,
        states={
            'readonly': Eval('purchase_state') != 'draft',
            },
        depends=['purchase_state'])
    quantity = fields.Float('Quantity',
        digits=(16, Eval('unit_digits', 2)),
        states={
            'invisible': Eval('type') != 'line',
            'required': Eval('type') == 'line',
            'readonly': Eval('purchase_state') != 'draft',
            },
        depends=['unit_digits', 'type', 'purchase_state'])
    unit = fields.Many2One('product.uom', 'Unit',
        ondelete='RESTRICT',
        states={
            'required': Bool(Eval('product')),
            'invisible': Eval('type') != 'line',
            'readonly': Eval('purchase_state') != 'draft',
            },
        domain=[
            If(Bool(Eval('product_uom_category')),
                ('category', '=', Eval('product_uom_category')),
                ('category', '!=', -1)),
            ],
        depends=['product', 'type', 'product_uom_category', 'purchase_state'])
    unit_digits = fields.Function(fields.Integer('Unit Digits'),
        'on_change_with_unit_digits')
    product = fields.Many2One('product.product', 'Product',
        ondelete='RESTRICT',
        domain=[
            ('purchasable', '=', True),
            If(Bool(Eval('product_supplier')),
                ('product_suppliers', '=', Eval('product_supplier')),
                ()),
            ],
        states={
            'invisible': Eval('type') != 'line',
            'readonly': Eval('purchase_state') != 'draft',
            'required': Bool(Eval('product_supplier')),
            },
        context={
            'locations': If(Bool(Eval('_parent_purchase', {}).get(
                        'warehouse')),
                [Eval('_parent_purchase', {}).get('warehouse', None)],
                []),
            'stock_date_end': Eval('_parent_purchase', {}).get(
                'purchase_date'),
            'stock_skip_warehouse': True,
            # From _get_context_purchase_price
            'currency': Eval('_parent_purchase', {}).get('currency'),
            'supplier': Eval('_parent_purchase', {}).get('party'),
            'purchase_date': Eval('_parent_purchase', {}).get('purchase_date'),
            'uom': Eval('unit'),
            'taxes': Eval('taxes', []),
            'quantity': Eval('quantity'),
            }, depends=['type', 'purchase_state', 'product_supplier'])
    product_supplier = fields.Many2One(
        'purchase.product_supplier', "Supplier's Product",
        ondelete='RESTRICT',
        domain=[
            If(Bool(Eval('product')),
                ('product.products', '=', Eval('product')),
                ()),
            ('party', '=', Eval('_parent_purchase', {}).get('party')),
            ],
        states={
            'invisible': Eval('type') != 'line',
            'readonly': Eval('purchase_state') != 'draft',
            },
        depends=['product', 'type', 'purchase_state'])
    product_uom_category = fields.Function(
        fields.Many2One('product.uom.category', 'Product Uom Category'),
        'on_change_with_product_uom_category')
    unit_price = fields.Numeric('Unit Price', digits=price_digits,
        states={
            'invisible': Eval('type') != 'line',
            'required': Eval('type') == 'line',
            'readonly': Eval('purchase_state') != 'draft',
            }, depends=['type', 'purchase_state'])
    amount = fields.Function(fields.Numeric('Amount',
            digits=(16,
                Eval('_parent_purchase', {}).get('currency_digits', 2)),
            states={
                'invisible': ~Eval('type').in_(['line', 'subtotal']),
                },
            depends=['type']), 'get_amount')
    description = fields.Text('Description', size=None,
        states={
            'readonly': Eval('purchase_state') != 'draft',
            },
        depends=['purchase_state'])
    note = fields.Text('Note')
    taxes = fields.Many2Many('purchase.line-account.tax',
        'line', 'tax', 'Taxes',
        order=[('tax.sequence', 'ASC'), ('tax.id', 'ASC')],
        domain=[('parent', '=', None), ['OR',
                ('group', '=', None),
                ('group.kind', 'in', ['purchase', 'both'])],
                ('company', '=',
                    Eval('_parent_purchase', {}).get('company', -1)),
            ],
        states={
            'invisible': Eval('type') != 'line',
            'readonly': Eval('purchase_state') != 'draft',
            }, depends=['type', 'purchase_state'])
    invoice_lines = fields.One2Many('account.invoice.line', 'origin',
        'Invoice Lines', readonly=True)
    moves = fields.One2Many('stock.move', 'origin', 'Moves', readonly=True)
    moves_ignored = fields.Many2Many('purchase.line-ignored-stock.move',
            'purchase_line', 'move', 'Ignored Moves', readonly=True)
    moves_recreated = fields.Many2Many('purchase.line-recreated-stock.move',
            'purchase_line', 'move', 'Recreated Moves', readonly=True)
    move_done = fields.Function(fields.Boolean('Moves Done'), 'get_move_done')
    move_exception = fields.Function(fields.Boolean('Moves Exception'),
            'get_move_exception')
    from_location = fields.Function(fields.Many2One('stock.location',
            'From Location'), 'get_from_location')
    to_location = fields.Function(fields.Many2One('stock.location',
            'To Location'), 'get_to_location')
    delivery_date = fields.Function(fields.Date('Delivery Date',
            states={
                'invisible': ((Eval('type') != 'line')
                    | Eval('delivery_date_edit', False)),
                },
            depends=['type', 'delivery_date_edit']),
        'on_change_with_delivery_date')
    delivery_date_edit = fields.Boolean(
        "Edit Delivery Date",
        states={
            'invisible': Eval('type') != 'line',
            'readonly': Eval('purchase_state').in_([
                    'processing', 'done', 'cancel']),
            },
        depends=['type', 'purchase_state'],
        help="Check to edit the delivery date.")
    delivery_date_store = fields.Date(
        "Delivery Date",
        states={
            'invisible': ((Eval('type') != 'line')
                | ~Eval('delivery_date_edit', False)),
            'readonly': Eval('purchase_state').in_([
                    'processing', 'done', 'cancel']),
            },
        depends=['type', 'delivery_date_edit', 'purchase_state'])
    purchase_state = fields.Function(
        fields.Selection(STATES, 'Purchase State'),
        'on_change_with_purchase_state')

    @classmethod
    def __setup__(cls):
        super(PurchaseLine, cls).__setup__()
        cls._error_messages.update({
                'supplier_location_required': ('Purchase "%(purchase)s" '
                    'misses the supplier location for line "%(line)s".'),
                'missing_account_expense': ('Product "%(product)s" of '
                    'purchase %(purchase)s misses an expense account.'),
                'missing_default_account_expense': ('Purchase "%(purchase)s" '
                    'misses a default "account expense".'),
                'delete_cancel_draft': ('The line "%(line)s" must be on '
                    'canceled or draft purchase to be deleted.'),
                })

    @classmethod
    def __register__(cls, module_name):
        super(PurchaseLine, cls).__register__(module_name)
        table = cls.__table_handler__(module_name)

        # Migration from 4.6: drop required on description
        table.not_null_action('description', action='remove')

    @staticmethod
    def default_type():
        return 'line'

    @classmethod
    def default_delivery_date_edit(cls):
        return False

    @property
    def _move_remaining_quantity(self):
        "Compute the remaining quantity to receive"
        pool = Pool()
        Uom = pool.get('product.uom')
        if self.type != 'line' or not self.product:
            return
        if self.product.type == 'service':
            return
        skip_ids = set(x.id for x in self.moves_ignored)
        quantity = abs(self.quantity)
        for move in self.moves:
            if move.state == 'done' or move.id in skip_ids:
                quantity -= Uom.compute_qty(move.uom, move.quantity, self.unit)
        return quantity

    def get_move_done(self, name):
        quantity = self._move_remaining_quantity
        if quantity is None:
            return True
        else:
            return self.unit.round(quantity) <= 0

    def get_move_exception(self, name):
        skip_ids = set(x.id for x in self.moves_ignored
            + self.moves_recreated)
        for move in self.moves:
            if move.state == 'cancel' \
                    and move.id not in skip_ids:
                return True
        return False

    @fields.depends('unit')
    def on_change_with_unit_digits(self, name=None):
        if self.unit:
            return self.unit.digits
        return 2

    def _get_tax_rule_pattern(self):
        '''
        Get tax rule pattern
        '''
        return {}

    @fields.depends('purchase', '_parent_purchase.currency',
        '_parent_purchase.party', '_parent_purchase.purchase_date',
        'unit', 'product', 'product_supplier', 'taxes')
    def _get_context_purchase_price(self):
        context = {}
        if self.purchase:
            if self.purchase.currency:
                context['currency'] = self.purchase.currency.id
            if self.purchase.party:
                context['supplier'] = self.purchase.party.id
            context['purchase_date'] = self.purchase.purchase_date
        if self.unit:
            context['uom'] = self.unit.id
        elif self.product:
            context['uom'] = self.product.purchase_uom.id
        if self.product_supplier:
            context['product_supplier'] = self.product_supplier.id
        context['taxes'] = [t.id for t in self.taxes or []]
        return context

    @fields.depends('product', 'unit', 'quantity', 'purchase',
        '_parent_purchase.party', 'product_supplier',
        methods=['_get_tax_rule_pattern', '_get_context_purchase_price'])
    def on_change_product(self):
        pool = Pool()
        Product = pool.get('product.product')

        if not self.product:
            return

        party = None
        if self.purchase and self.purchase.party:
            party = self.purchase.party

        # Set taxes before unit_price to have taxes in context of purchase
        # price
        taxes = []
        pattern = self._get_tax_rule_pattern()
        for tax in self.product.supplier_taxes_used:
            if party and party.supplier_tax_rule:
                tax_ids = party.supplier_tax_rule.apply(tax, pattern)
                if tax_ids:
                    taxes.extend(tax_ids)
                continue
            taxes.append(tax.id)
        if party and party.supplier_tax_rule:
            tax_ids = party.supplier_tax_rule.apply(None, pattern)
            if tax_ids:
                taxes.extend(tax_ids)
        self.taxes = taxes

        category = self.product.purchase_uom.category
        if not self.unit or self.unit.category != category:
            self.unit = self.product.purchase_uom
            self.unit_digits = self.product.purchase_uom.digits

        if self.purchase and self.purchase.party:
            product_suppliers = [ps for ps in self.product.product_suppliers
                if ps.party == self.purchase.party]
            if len(product_suppliers) == 1:
                self.product_supplier, = product_suppliers
        if (self.product_supplier
                and (self.product_supplier
                    not in self.product.product_suppliers)):
            self.product_supplier = None

        with Transaction().set_context(self._get_context_purchase_price()):
            self.unit_price = Product.get_purchase_price([self.product],
                abs(self.quantity or 0))[self.product.id]
            if self.unit_price:
                self.unit_price = self.unit_price.quantize(
                    Decimal(1) / 10 ** self.__class__.unit_price.digits[1])

        self.type = 'line'
        self.amount = self.on_change_with_amount()

    @fields.depends('product', 'product_supplier',
        methods=['on_change_product'])
    def on_change_product_supplier(self):
        if not self.product and self.product_supplier:
            if len(self.product_supplier.product.products) == 1:
                self.product, = self.product_supplier.product.products
        self.on_change_product()

    @fields.depends('product')
    def on_change_with_product_uom_category(self, name=None):
        if self.product:
            return self.product.default_uom_category.id

    @fields.depends('product', 'quantity', 'unit',
        methods=['_get_context_purchase_price'])
    def on_change_quantity(self):
        Product = Pool().get('product.product')

        if not self.product:
            return

        with Transaction().set_context(self._get_context_purchase_price()):
            self.unit_price = Product.get_purchase_price([self.product],
                abs(self.quantity or 0))[self.product.id]
            if self.unit_price:
                self.unit_price = self.unit_price.quantize(
                    Decimal(1) / 10 ** self.__class__.unit_price.digits[1])

    @fields.depends(methods=['on_change_quantity'])
    def on_change_unit(self):
        self.on_change_quantity()

    @fields.depends(methods=['on_change_quantity'])
    def on_change_taxes(self):
        self.on_change_quantity()

    @fields.depends('type', 'quantity', 'unit_price', 'unit', 'purchase',
        '_parent_purchase.currency')
    def on_change_with_amount(self):
        if self.type == 'line':
            currency = self.purchase.currency if self.purchase else None
            amount = Decimal(str(self.quantity or '0.0')) * \
                (self.unit_price or Decimal('0.0'))
            if currency:
                return currency.round(amount)
            return amount
        return Decimal('0.0')

    def get_amount(self, name):
        if self.type == 'line':
            return self.on_change_with_amount()
        elif self.type == 'subtotal':
            amount = Decimal('0.0')
            for line2 in self.purchase.lines:
                if line2.type == 'line':
                    amount += line2.purchase.currency.round(
                        Decimal(str(line2.quantity)) * line2.unit_price)
                elif line2.type == 'subtotal':
                    if self == line2:
                        break
                    amount = Decimal('0.0')
            return amount
        return Decimal('0.0')

    def get_from_location(self, name):
        if (self.quantity or 0) >= 0:
            return self.purchase.party.supplier_location.id
        elif self.purchase.return_from_location:
            return self.purchase.return_from_location.id

    def get_to_location(self, name):
        if (self.quantity or 0) >= 0:
            if self.purchase.warehouse:
                return self.purchase.warehouse.input_location.id
        else:
            return self.purchase.party.supplier_location.id

    @fields.depends('product_supplier', 'quantity', 'moves', 'purchase',
        '_parent_purchase.purchase_date',
        'delivery_date_edit', 'delivery_date_store',
        '_parent_purchase.delivery_date')
    def on_change_with_delivery_date(self, name=None):
        pool = Pool()
        Date = pool.get('ir.date')
        if self.moves:
            dates = filter(
                None, (m.effective_date or m.planned_date for m in self.moves
                    if m.state != 'cancel'))
            return min(dates, default=None)
        delivery_date = None
        if self.delivery_date_edit:
            delivery_date = self.delivery_date_store
        elif self.purchase and self.purchase.delivery_date:
            delivery_date = self.purchase.delivery_date
        elif (self.product_supplier
                and self.quantity is not None
                and self.quantity > 0
                and self.purchase):
            date = self.purchase.purchase_date if self.purchase else None
            delivery_date = self.product_supplier.compute_supply_date(
                date=date)
            if delivery_date == datetime.date.max:
                delivery_date = None
        if delivery_date and delivery_date < Date.today():
            delivery_date = None
        return delivery_date

    @fields.depends('purchase', '_parent_purchase.state')
    def on_change_with_purchase_state(self, name=None):
        if self.purchase:
            return self.purchase.state

    def get_invoice_line(self):
        'Return a list of invoice line for purchase line'
        pool = Pool()
        InvoiceLine = pool.get('account.invoice.line')
        AccountConfiguration = pool.get('account.configuration')
        account_config = AccountConfiguration(1)

        invoice_line = InvoiceLine()
        invoice_line.type = self.type
        invoice_line.description = self.description
        invoice_line.note = self.note
        invoice_line.origin = self
        if self.type != 'line':
            if self._get_invoice_not_line():
                return [invoice_line]
            else:
                return []

        quantity = (self._get_invoice_line_quantity()
            - self._get_invoiced_quantity())

        if self.unit:
            quantity = self.unit.round(quantity)
        invoice_line.quantity = quantity

        if not invoice_line.quantity:
            return []

        invoice_line.unit = self.unit
        invoice_line.product = self.product
        invoice_line.unit_price = self.unit_price
        invoice_line.taxes = self.taxes
        invoice_line.invoice_type = 'in'
        if self.product:
            invoice_line.account = self.product.account_expense_used
            if not invoice_line.account:
                self.raise_user_error('missing_account_expense', {
                        'product': invoice_line.product.rec_name,
                        'purchase': self.purchase.rec_name,
                        })
        else:
            invoice_line.account = account_config.get_multivalue(
                'default_category_account_expense')
            if not invoice_line.account:
                self.raise_user_error('missing_default_account_expense',
                    {'purchase': self.purchase.rec_name})
        invoice_line.stock_moves = self._get_invoice_line_moves()
        return [invoice_line]

    def _get_invoice_not_line(self):
        'Return if the not line should be invoiced'
        return (self.purchase.invoice_method == 'order'
            and not self.invoice_lines)

    def _get_invoice_line_quantity(self):
        'Return the quantity that should be invoiced'
        pool = Pool()
        Uom = pool.get('product.uom')

        if (self.purchase.invoice_method == 'order'
                or not self.product
                or self.product.type == 'service'):
            return self.quantity
        elif self.purchase.invoice_method == 'shipment':
            quantity = 0.0
            for move in self.moves:
                if move.state == 'done':
                    quantity += Uom.compute_qty(move.uom, move.quantity,
                        self.unit)
            if self.quantity < 0:
                quantity *= -1
            return quantity

    def _get_invoiced_quantity(self):
        'Return the quantity already invoiced'
        pool = Pool()
        Uom = pool.get('product.uom')

        quantity = 0
        skips = {l for i in self.purchase.invoices_recreated for l in i.lines}
        for invoice_line in self.invoice_lines:
            if invoice_line.type != 'line':
                continue
            if invoice_line not in skips:
                quantity += Uom.compute_qty(invoice_line.unit,
                    invoice_line.quantity, self.unit)
        return quantity

    def _get_invoice_line_moves(self):
        'Return the stock moves that should be invoiced'
        moves = []
        if self.purchase.invoice_method == 'order':
            moves.extend(self.moves)
        elif self.purchase.invoice_method == 'shipment':
            for move in self.moves:
                if move.state == 'done':
                    if move.invoiced_quantity < move.quantity:
                        moves.append(move)
        return moves

    def get_move(self, move_type):
        '''
        Return move values for purchase line
        '''
        pool = Pool()
        Move = pool.get('stock.move')

        if self.type != 'line':
            return
        if not self.product:
            return
        if self.product.type == 'service':
            return

        if (self.quantity >= 0) != (move_type == 'in'):
            return

        quantity = (self._get_move_quantity(move_type)
            - self._get_shipped_quantity(move_type))

        quantity = self.unit.round(quantity)
        if quantity <= 0:
            return

        if not self.purchase.party.supplier_location:
            self.raise_user_error('supplier_location_required', {
                    'purchase': self.purchase.rec_name,
                    'line': self.rec_name,
                    })
        move = Move()
        move.quantity = quantity
        move.uom = self.unit
        move.product = self.product
        move.from_location = self.from_location
        move.to_location = self.to_location
        move.state = 'draft'
        move.company = self.purchase.company
        move.unit_price = self.unit_price
        move.currency = self.purchase.currency
        if self.moves:
            # backorder can not be planned
            move.planned_date = None
        else:
            move.planned_date = self.delivery_date
        move.invoice_lines = self._get_move_invoice_lines(move_type)
        move.origin = self
        return move

    def _get_move_quantity(self, move_type):
        'Return the quantity that should be shipped'
        return abs(self.quantity)

    def _get_shipped_quantity(self, move_type):
        'Return the quantity already shipped'
        pool = Pool()
        Uom = pool.get('product.uom')

        quantity = 0
        skip = set(self.moves_recreated)
        for move in self.moves:
            if move not in skip:
                quantity += Uom.compute_qty(move.uom, move.quantity,
                    self.unit)
        return quantity

    def _get_move_invoice_lines(self, move_type):
        'Return the invoice lines that should be shipped'
        if self.purchase.invoice_method == 'order':
            return [l for l in self.invoice_lines]
        else:
            return [l for l in self.invoice_lines if not l.stock_moves]

    def get_rec_name(self, name):
        if self.product:
            return '%s @ %s' % (self.product.rec_name, self.purchase.rec_name)
        else:
            return self.purchase.rec_name

    @classmethod
    def search_rec_name(cls, name, clause):
        return ['OR',
            ('purchase.rec_name',) + tuple(clause[1:]),
            ('product.rec_name',) + tuple(clause[1:]),
            ]

    @classmethod
    def view_attributes(cls):
        return [
            ('/form//field[@name="note"]|/form//field[@name="description"]',
                'spell', Eval('_parent_purchase', {}).get('party_lang')),
            ('//label[@id="delivery_date"]', 'states', {
                    'invisible': Eval('type') != 'line',
                    }),
            ]

    @classmethod
    def delete(cls, lines):
        for line in lines:
            if line.purchase_state not in {'cancel', 'draft'}:
                cls.raise_user_error('delete_cancel_draft', {
                        'line': line.rec_name,
                        })
        super(PurchaseLine, cls).delete(lines)

    @classmethod
    def copy(cls, lines, default=None):
        if default is None:
            default = {}
        else:
            default = default.copy()
        default.setdefault('moves', None)
        default.setdefault('moves_ignored', None)
        default.setdefault('moves_recreated', None)
        default.setdefault('invoice_lines', None)
        return super(PurchaseLine, cls).copy(lines, default=default)


class PurchaseLineTax(ModelSQL):
    'Purchase Line - Tax'
    __name__ = 'purchase.line-account.tax'
    _table = 'purchase_line_account_tax'
    line = fields.Many2One('purchase.line', 'Purchase Line',
            ondelete='CASCADE', select=True, required=True,
            domain=[('type', '=', 'line')])
    tax = fields.Many2One('account.tax', 'Tax', ondelete='RESTRICT',
            select=True, required=True, domain=[('parent', '=', None)])


class PurchaseLineIgnoredMove(ModelSQL):
    'Purchase Line - Ignored Move'
    __name__ = 'purchase.line-ignored-stock.move'
    _table = 'purchase_line_moves_ignored_rel'
    purchase_line = fields.Many2One('purchase.line', 'Purchase Line',
            ondelete='CASCADE', select=True, required=True)
    move = fields.Many2One('stock.move', 'Move', ondelete='RESTRICT',
            select=True, required=True)


class PurchaseLineRecreatedMove(ModelSQL):
    'Purchase Line - Ignored Move'
    __name__ = 'purchase.line-recreated-stock.move'
    _table = 'purchase_line_moves_recreated_rel'
    purchase_line = fields.Many2One('purchase.line', 'Purchase Line',
            ondelete='CASCADE', select=True, required=True)
    move = fields.Many2One('stock.move', 'Move', ondelete='RESTRICT',
            select=True, required=True)


class PurchaseReport(CompanyReport):
    __name__ = 'purchase.purchase'

    @classmethod
    def execute(cls, ids, data):
        with Transaction().set_context(address_with_party=True):
            return super(PurchaseReport, cls).execute(ids, data)

    @classmethod
    def get_context(cls, records, data):
        pool = Pool()
        Date = pool.get('ir.date')
        context = super(PurchaseReport, cls).get_context(records, data)
        context['today'] = Date.today()
        return context


class OpenSupplier(Wizard):
    'Open Suppliers'
    __name__ = 'purchase.open_supplier'
    start_state = 'open_'
    open_ = StateAction('party.act_party_form')

    def do_open_(self, action):
        pool = Pool()
        ModelData = pool.get('ir.model.data')
        Wizard = pool.get('ir.action.wizard')
        Purchase = pool.get('purchase.purchase')
        cursor = Transaction().connection.cursor()
        purchase = Purchase.__table__()

        cursor.execute(*purchase.select(purchase.party,
                group_by=purchase.party))
        supplier_ids = [line[0] for line in cursor.fetchall()]
        action['pyson_domain'] = PYSONEncoder().encode(
            [('id', 'in', supplier_ids)])
        wizard = Wizard(ModelData.get_id('purchase', 'act_open_supplier'))
        action['name'] = wizard.name
        return action, {}


class HandleShipmentExceptionAsk(ModelView):
    'Handle Shipment Exception'
    __name__ = 'purchase.handle.shipment.exception.ask'
    recreate_moves = fields.Many2Many(
        'stock.move', None, None, 'Recreate Moves',
        domain=[('id', 'in', Eval('domain_moves'))], depends=['domain_moves'],
        help=('The selected moves will be recreated. '
            'The other ones will be ignored.'))
    domain_moves = fields.Many2Many(
        'stock.move', None, None, 'Domain Moves')


class HandleShipmentException(Wizard):
    'Handle Shipment Exception'
    __name__ = 'purchase.handle.shipment.exception'
    start_state = 'ask'
    ask = StateView('purchase.handle.shipment.exception.ask',
        'purchase.handle_shipment_exception_ask_view_form', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('OK', 'handle', 'tryton-ok', default=True),
            ])
    handle = StateTransition()

    def default_ask(self, fields):
        Purchase = Pool().get('purchase.purchase')

        purchase = Purchase(Transaction().context['active_id'])

        moves = []
        for line in purchase.lines:
            skip = set(line.moves_ignored + line.moves_recreated)
            for move in line.moves:
                if move.state == 'cancel' and move not in skip:
                    moves.append(move.id)
        return {
            'recreate_moves': moves,
            'domain_moves': moves,
            }

    def transition_handle(self):
        pool = Pool()
        Purchase = pool.get('purchase.purchase')
        PurchaseLine = pool.get('purchase.line')
        to_recreate = self.ask.recreate_moves
        domain_moves = self.ask.domain_moves

        purchase = Purchase(Transaction().context['active_id'])

        for line in purchase.lines:
            moves_ignored = []
            moves_recreated = []
            skip = set(line.moves_ignored)
            skip.update(line.moves_recreated)
            for move in line.moves:
                if move not in domain_moves or move in skip:
                    continue
                if move in to_recreate:
                    moves_recreated.append(move.id)
                else:
                    moves_ignored.append(move.id)

            PurchaseLine.write([line], {
                'moves_ignored': [('add', moves_ignored)],
                'moves_recreated': [('add', moves_recreated)],
                })

        Purchase.__queue__.process([purchase])
        return 'end'


class HandleInvoiceExceptionAsk(ModelView):
    'Handle Invoice Exception'
    __name__ = 'purchase.handle.invoice.exception.ask'
    recreate_invoices = fields.Many2Many(
        'account.invoice', None, None, 'Recreate Invoices',
        domain=[('id', 'in', Eval('domain_invoices'))],
        depends=['domain_invoices'],
        help=('The selected invoices will be recreated. '
            'The other ones will be ignored.'))
    domain_invoices = fields.Many2Many(
        'account.invoice', None, None, 'Domain Invoices')


class HandleInvoiceException(Wizard):
    'Handle Invoice Exception'
    __name__ = 'purchase.handle.invoice.exception'
    start_state = 'ask'
    ask = StateView('purchase.handle.invoice.exception.ask',
        'purchase.handle_invoice_exception_ask_view_form', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('OK', 'handle', 'tryton-ok', default=True),
            ])
    handle = StateTransition()

    def default_ask(self, fields):
        Purchase = Pool().get('purchase.purchase')

        purchase = Purchase(Transaction().context['active_id'])
        skip = set(purchase.invoices_ignored)
        skip.update(purchase.invoices_recreated)
        invoices = []
        for invoice in purchase.invoices:
            if invoice.state == 'cancel' and invoice not in skip:
                invoices.append(invoice.id)
        return {
            'recreate_invoices': invoices,
            'domain_invoices': invoices,
            }

    def transition_handle(self):
        Purchase = Pool().get('purchase.purchase')

        purchase = Purchase(Transaction().context['active_id'])

        invoices_ignored = []
        invoices_recreated = []
        for invoice in self.ask.domain_invoices:
            if invoice in self.ask.recreate_invoices:
                invoices_recreated.append(invoice.id)
            else:
                invoices_ignored.append(invoice.id)

        Purchase.write([purchase], {
            'invoices_ignored': [('add', invoices_ignored)],
            'invoices_recreated': [('add', invoices_recreated)],
            })

        Purchase.__queue__.process([purchase])
        return 'end'


class ModifyHeaderStateView(StateView):
    def get_view(self, wizard, state_name):
        with Transaction().set_context(modify_header=True):
            return super(ModifyHeaderStateView, self).get_view(
                wizard, state_name)


class ModifyHeader(Wizard):
    "Modify Header"
    __name__ = 'purchase.modify_header'
    start = ModifyHeaderStateView('purchase.purchase',
        'purchase.modify_header_form', [
            Button("Cancel", 'end', 'tryton-cancel'),
            Button("Modify", 'modify', 'tryton-ok', default=True),
            ])
    modify = StateTransition()

    @classmethod
    def __setup__(cls):
        super(ModifyHeader, cls).__setup__()
        cls._error_messages.update({
                'not_in_draft': (
                    'The purchase "%(purchase)s" must be in draft '
                    'to modify header.'),
                })

    def get_purchase(self):
        pool = Pool()
        Purchase = pool.get('purchase.purchase')

        purchase = Purchase(Transaction().context['active_id'])
        if purchase.state != 'draft':
            self.raise_user_error('not_in_draft', {
                    'purchase': purchase.rec_name,
                    })
        return purchase

    def default_start(self, fields):
        purchase = self.get_purchase()
        defaults = {}
        for fieldname in fields:
            value = getattr(purchase, fieldname)
            if isinstance(value, Model):
                if getattr(purchase.__class__, fieldname)._type == 'reference':
                    value = str(value)
                else:
                    value = value.id
            elif isinstance(value, (list, tuple)):
                value = [r.id for r in value]
            defaults[fieldname] = value

        # Mimic an empty sale in draft state to get the fields' states right
        defaults['lines'] = []
        return defaults

    def transition_modify(self):
        pool = Pool()
        Line = pool.get('purchase.line')

        purchase = self.get_purchase()
        purchase.__class__.write([purchase], self.start._save_values)

        # Call on_change after the save to ensure parent sale
        # has the modified values
        for line in purchase.lines:
            line.on_change_product()
        Line.save(purchase.lines)

        return 'end'
