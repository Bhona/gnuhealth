# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.
import datetime

from sql import Literal
from sql.aggregate import Count

from trytond.model import ModelView, ModelSQL, MatchMixin, fields, \
    sequence_ordered
from trytond.pyson import Eval, If
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction

from trytond.modules.product import price_digits

__all__ = ['Template', 'Product', 'ProductSupplier', 'ProductSupplierPrice']


class Template(metaclass=PoolMeta):
    __name__ = "product.template"
    purchasable = fields.Boolean('Purchasable', states={
            'readonly': ~Eval('active', True),
            }, depends=['active'])
    product_suppliers = fields.One2Many('purchase.product_supplier',
        'product', 'Suppliers', states={
            'readonly': ~Eval('active', True),
            'invisible': (~Eval('purchasable', False)
                | ~Eval('context', {}).get('company')),
            }, depends=['active', 'purchasable'])
    purchase_uom = fields.Many2One('product.uom', 'Purchase UOM', states={
            'readonly': ~Eval('active'),
            'invisible': ~Eval('purchasable'),
            'required': Eval('purchasable', False),
            },
        domain=[('category', '=', Eval('default_uom_category'))],
        depends=['active', 'purchasable', 'default_uom_category'])

    @classmethod
    def __setup__(cls):
        super(Template, cls).__setup__()
        cls._error_messages.update({
                'change_purchase_uom': ('Purchase prices are based '
                    'on the purchase uom.'),
                })

    @fields.depends('default_uom', 'purchase_uom', 'purchasable')
    def on_change_default_uom(self):
        try:
            super(Template, self).on_change_default_uom()
        except AttributeError:
            pass
        if self.default_uom:
            if self.purchase_uom:
                if self.default_uom.category != self.purchase_uom.category:
                    self.purchase_uom = self.default_uom
            else:
                self.purchase_uom = self.default_uom

    @classmethod
    def view_attributes(cls):
        return super(Template, cls).view_attributes() + [
            ('//page[@id="suppliers"]', 'states', {
                    'invisible': ~Eval('purchasable'),
                    })]

    @classmethod
    def write(cls, *args):
        actions = iter(args)
        for templates, values in zip(actions, actions):
            if not values.get("purchase_uom"):
                continue
            for template in templates:
                if not template.purchase_uom:
                    continue
                if template.purchase_uom.id == values["purchase_uom"]:
                    continue
                for product in template.products:
                    if not product.product_suppliers:
                        continue
                    cls.raise_user_warning(
                            '%s@product_template' % template.id,
                            'change_purchase_uom')
        super(Template, cls).write(*args)

    @classmethod
    def copy(cls, templates, default=None):
        pool = Pool()
        ProductSupplier = pool.get('purchase.product_supplier')
        if default is None:
            default = {}
        else:
            default = default.copy()

        copy_suppliers = 'product_suppliers' not in default
        default.setdefault('product_suppliers', None)
        new_templates = super().copy(templates, default)
        if copy_suppliers:
            old2new = {}
            to_copy = []
            for template, new_template in zip(templates, new_templates):
                to_copy.extend(
                    ps for ps in template.product_suppliers if not ps.product)
                old2new[template.id] = new_template.id
            if to_copy:
                ProductSupplier.copy(to_copy, {
                        'template': lambda d: old2new[d['template']],
                        })
        return new_templates


class Product(metaclass=PoolMeta):
    __name__ = 'product.product'

    purchase_price_uom = fields.Function(fields.Numeric(
            "Purchase Price", digits=price_digits), 'get_purchase_price_uom')

    @classmethod
    def get_purchase_price_uom(cls, products, name):
        quantity = Transaction().context.get('quantity') or 0
        return cls.get_purchase_price(products, quantity=quantity)

    @classmethod
    def get_purchase_price(cls, products, quantity=0):
        '''
        Return purchase price for product ids.
        The context that can have as keys:
            uom: the unit of measure
            supplier: the supplier party id
            currency: the currency id for the returned price
        '''
        pool = Pool()
        Uom = pool.get('product.uom')
        User = pool.get('res.user')
        Currency = pool.get('currency.currency')
        Date = pool.get('ir.date')
        ProductSupplier = pool.get('purchase.product_supplier')
        ProductSupplierPrice = pool.get('purchase.product_supplier.price')

        today = Date.today()
        context = Transaction().context
        prices = {}

        assert len(products) == len(set(products)), "Duplicate products"

        uom = None
        if context.get('uom'):
            uom = Uom(context['uom'])

        currency = None
        if context.get('currency'):
            currency = Currency(context['currency'])

        user = User(Transaction().user)

        for product in products:
            prices[product.id] = product.cost_price
            default_uom = product.default_uom
            default_currency = (user.company.currency if user.company
                else None)
            if not uom or default_uom.category != uom.category:
                product_uom = default_uom
            else:
                product_uom = uom
            pattern = ProductSupplier.get_pattern()
            product_suppliers = product.product_suppliers
            if context.get('product_supplier'):
                for product_supplier in product.product_suppliers:
                    if product_supplier.id == context['product_supplier']:
                        product_suppliers = [product_supplier]
                        break
            for product_supplier in product_suppliers:
                if product_supplier.match(pattern):
                    pattern = ProductSupplierPrice.get_pattern()
                    for price in product_supplier.prices:
                        if price.match(quantity, product_uom, pattern):
                            prices[product.id] = price.unit_price
                            default_uom = product_supplier.uom
                            default_currency = product_supplier.currency
                    break
            prices[product.id] = Uom.compute_price(
                default_uom, prices[product.id], product_uom)
            if currency and default_currency:
                date = context.get('purchase_date') or today
                with Transaction().set_context(date=date):
                    prices[product.id] = Currency.compute(default_currency,
                        prices[product.id], currency, round=False)
        return prices

    @classmethod
    def copy(cls, products, default=None):
        pool = Pool()
        ProductSupplier = pool.get('purchase.product_supplier')
        if default is None:
            default = {}
        else:
            default = default.copy()

        copy_suppliers = 'product_suppliers' not in default
        if 'template' in default:
            default.setdefault('product_suppliers', None)
        new_products = super().copy(products, default)
        if 'template' in default and copy_suppliers:
            template2new = {}
            product2new = {}
            to_copy = []
            for product, new_product in zip(products, new_products):
                if product.product_suppliers:
                    to_copy.extend(product.product_suppliers)
                    template2new[product.template.id] = new_product.template.id
                    product2new[product.id] = new_product.id
            if to_copy:
                ProductSupplier.copy(to_copy, {
                        'product': lambda d: product2new[d['product']],
                        'template': lambda d: template2new[d['template']],
                        })
        return new_products


class ProductSupplier(sequence_ordered(), ModelSQL, ModelView, MatchMixin):
    'Product Supplier'
    __name__ = 'purchase.product_supplier'
    product = fields.Many2One('product.template', 'Product', required=True,
            ondelete='CASCADE', select=True)
    party = fields.Many2One('party.party', 'Supplier', required=True,
        ondelete='CASCADE', select=True)
    name = fields.Char('Name', size=None, translate=True, select=True)
    code = fields.Char('Code', size=None, select=True)
    prices = fields.One2Many('purchase.product_supplier.price',
            'product_supplier', 'Prices')
    company = fields.Many2One('company.company', 'Company', required=True,
        ondelete='CASCADE', select=True,
        domain=[
            ('id', If(Eval('context', {}).contains('company'), '=', '!='),
                Eval('context', {}).get('company', -1)),
            ])
    lead_time = fields.TimeDelta('Lead Time')
    currency = fields.Many2One('currency.currency', 'Currency', required=True,
        ondelete='RESTRICT')

    @classmethod
    def __register__(cls, module_name):
        transaction = Transaction()
        cursor = transaction.connection.cursor()
        table = cls.__table_handler__(module_name)
        sql_table = cls.__table__()

        super(ProductSupplier, cls).__register__(module_name)

        # Migration from 3.8: change delivery_time inte timedelta lead_time
        if table.column_exist('delivery_time'):
            cursor.execute(*sql_table.select(
                    sql_table.id, sql_table.delivery_time))
            for id_, delivery_time in cursor.fetchall():
                if delivery_time is None:
                    continue
                lead_time = datetime.timedelta(days=delivery_time)
                cursor.execute(*sql_table.update(
                        [sql_table.lead_time],
                        [lead_time],
                        where=sql_table.id == id_))
            table.drop_column('delivery_time')

    @staticmethod
    def default_company():
        return Transaction().context.get('company')

    @staticmethod
    def default_currency():
        Company = Pool().get('company.company')
        if Transaction().context.get('company'):
            company = Company(Transaction().context['company'])
            return company.currency.id

    @fields.depends('party')
    def on_change_party(self):
        cursor = Transaction().connection.cursor()
        self.currency = self.default_currency()
        if self.party:
            table = self.__table__()
            cursor.execute(*table.select(table.currency,
                    where=table.party == self.party.id,
                    group_by=table.currency,
                    order_by=Count(Literal(1)).desc))
            row = cursor.fetchone()
            if row:
                self.currency, = row

    def get_rec_name(self, name):
        if self.code:
            name = self.name or self.product.name
            return '[' + self.code + '] ' + name
        else:
            return self.name or self.product.rec_name

    @classmethod
    def search_rec_name(cls, name, clause):
        if clause[1].startswith('!') or clause[1].startswith('not '):
            bool_op = 'AND'
        else:
            bool_op = 'OR'
        domain = [bool_op,
            ('product',) + tuple(clause[1:]),
            ('party',) + tuple(clause[1:]),
            ('code',) + tuple(clause[1:]),
            ('name',) + tuple(clause[1:]),
            ]
        return domain

    @property
    def uom(self):
        return self.product.purchase_uom

    def compute_supply_date(self, date=None):
        '''
        Compute the supply date for the Product Supplier at the given date
        '''
        Date = Pool().get('ir.date')

        if not date:
            date = Date.today()
        if self.lead_time is None:
            return datetime.date.max
        return date + self.lead_time

    def compute_purchase_date(self, date):
        '''
        Compute the purchase date for the Product Supplier at the given date
        '''
        Date = Pool().get('ir.date')

        if self.lead_time is None:
            return Date.today()
        return date - self.lead_time

    @staticmethod
    def get_pattern():
        context = Transaction().context
        return {
            'party': context.get('supplier'),
            }


class ProductSupplierPrice(
        sequence_ordered(), ModelSQL, ModelView, MatchMixin):
    'Product Supplier Price'
    __name__ = 'purchase.product_supplier.price'
    product_supplier = fields.Many2One('purchase.product_supplier',
            'Supplier', required=True, ondelete='CASCADE')
    quantity = fields.Float(
        "Quantity",
        required=True,
        domain=[('quantity', '>=', 0)],
        help='Minimal quantity')
    unit_price = fields.Numeric('Unit Price', required=True,
        digits=price_digits)

    @classmethod
    def __register__(cls, module_name):
        cursor = Transaction().connection.cursor()
        table = cls.__table_handler__(module_name)
        sql_table = cls.__table__()

        fill_sequence = not table.column_exist('sequence')

        super(ProductSupplierPrice, cls).__register__(module_name)

        # Migration from 3.2: replace quantity by sequence for order
        if fill_sequence:
            cursor.execute(*sql_table.update(
                    [sql_table.sequence], [sql_table.quantity]))

    @staticmethod
    def default_quantity():
        return 0.0

    @staticmethod
    def get_pattern():
        return {}

    def match(self, quantity, uom, pattern):
        pool = Pool()
        Uom = pool.get('product.uom')
        test_quantity = Uom.compute_qty(
            self.product_supplier.uom, self.quantity, uom)
        if test_quantity > abs(quantity):
            return False
        return super(ProductSupplierPrice, self).match(pattern)
