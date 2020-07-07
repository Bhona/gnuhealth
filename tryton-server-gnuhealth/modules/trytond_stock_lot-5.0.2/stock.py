# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.
import datetime
from collections import defaultdict
from functools import wraps

from trytond.model import ModelView, ModelSQL, fields
from trytond.pyson import Eval
from trytond.pool import Pool, PoolMeta
from trytond.tools import grouped_slice
from trytond.transaction import Transaction
from trytond.modules.stock import StockMixin

__all__ = ['Lot', 'LotType', 'Move', 'ShipmentIn', 'ShipmentOut',
    'LotByLocationContext', 'ShipmentOutReturn',
    'Period', 'PeriodCacheLot',
    'Inventory', 'InventoryLine', 'InventoryCount', 'InventoryCountSearch',
    'LotByLocationContext']


def check_no_move(func):
    def find_moves(cls, records, state=None):
        pool = Pool()
        Move = pool.get('stock.move')
        for sub_records in grouped_slice(records):
            domain = [
                ('lot', 'in', [r.id for r in sub_records])
                ]
            if state:
                domain.append(('state', '=', state))
            moves = Move.search(domain, limit=1, order=[])
            if moves:
                return True
        return False

    @wraps(func)
    def decorator(cls, *args):
        transaction = Transaction()
        if (transaction.user != 0
                and transaction.context.get('_check_access')):
            actions = iter(args)
            for records, values in zip(actions, actions):
                for field, state, error in cls._modify_no_move:
                    if field in values:
                        if find_moves(cls, records, state):
                            cls.raise_user_error(error)
                        # No moves for those records
                        break
        func(cls, *args)
    return decorator


class Lot(ModelSQL, ModelView, StockMixin):
    "Stock Lot"
    __name__ = 'stock.lot'
    _rec_name = 'number'
    number = fields.Char('Number', required=True, select=True)
    product = fields.Many2One('product.product', 'Product', required=True)
    quantity = fields.Function(fields.Float('Quantity'), 'get_quantity',
        searcher='search_quantity')
    forecast_quantity = fields.Function(fields.Float('Forecast Quantity'),
        'get_quantity', searcher='search_quantity')

    @classmethod
    def __setup__(cls):
        super(Lot, cls).__setup__()
        cls._error_messages.update({
                'change_product': ("You cannot change the product of a lot "
                    "which is associated to stock moves."),
                })
        cls._modify_no_move = [
            ('product', None, 'change_product'),
            ]

    @classmethod
    def get_quantity(cls, lots, name):
        location_ids = Transaction().context.get('locations')
        product_ids = list(set(l.product.id for l in lots))
        return cls._get_quantity(lots, name, location_ids,
            grouping=('product', 'lot',), grouping_filter=(product_ids,))

    @classmethod
    def search_quantity(cls, name, domain=None):
        location_ids = Transaction().context.get('locations')
        return cls._search_quantity(name, location_ids, domain,
            grouping=('product', 'lot'))

    @classmethod
    @check_no_move
    def write(cls, *args):
        super(Lot, cls).write(*args)


class LotByLocationContext(ModelView):
    'Lot by Location'
    __name__ = 'stock.lots_by_location.context'
    forecast_date = fields.Date(
        'At Date', help=('Allow to compute expected '
            'stock quantities for this date.\n'
            '* An empty value is an infinite date in the future.\n'
            '* A date in the past will provide historical values.'))
    stock_date_end = fields.Function(fields.Date('At Date'),
        'on_change_with_stock_date_end')

    @staticmethod
    def default_forecast_date():
        Date = Pool().get('ir.date')
        return Date.today()

    @fields.depends('forecast_date')
    def on_change_with_stock_date_end(self, name=None):
        if self.forecast_date is None:
            return datetime.date.max
        return self.forecast_date


class LotType(ModelSQL, ModelView):
    "Stock Lot Type"
    __name__ = 'stock.lot.type'
    name = fields.Char('Name', required=True, translate=True)
    code = fields.Char('Code', required=True)


class Move(metaclass=PoolMeta):
    __name__ = 'stock.move'
    lot = fields.Many2One('stock.lot', 'Lot',
        domain=[
            ('product', '=', Eval('product')),
            ],
        states={
            'readonly': Eval('state').in_(['cancel', 'done']),
            },
        depends=['state', 'product'])

    @classmethod
    def __setup__(cls):
        super(Move, cls).__setup__()
        cls._error_messages.update({
                'lot_required': 'Lot is required for move of product "%s".',
                })

    def check_lot(self):
        "Check if lot is required"
        if (self.state == 'done'
                and self.internal_quantity
                and not self.lot
                and self.product.lot_is_required(
                    self.from_location, self.to_location)):
            self.raise_user_error('lot_required', self.product.rec_name)

    @classmethod
    def validate(cls, moves):
        super(Move, cls).validate(moves)
        for move in moves:
            move.check_lot()


class ShipmentIn(metaclass=PoolMeta):
    __name__ = 'stock.shipment.in'

    @classmethod
    def _get_inventory_moves(cls, incoming_move):
        move = super(ShipmentIn, cls)._get_inventory_moves(incoming_move)
        if move and incoming_move.lot:
            move.lot = incoming_move.lot
        return move


class ShipmentOut(metaclass=PoolMeta):
    __name__ = 'stock.shipment.out'

    @classmethod
    def _sync_inventory_to_outgoing(cls, shipments, create=True, write=True):
        pool = Pool()
        Uom = pool.get('product.uom')
        Move = pool.get('stock.move')
        super(ShipmentOut, cls)._sync_inventory_to_outgoing(
            shipments, create=create, write=write)
        to_write = []
        for shipment in shipments:
            outgoing_by_product = defaultdict(list)
            for move in shipment.outgoing_moves:
                outgoing_by_product[move.product.id].append(move)
            for move in shipment.inventory_moves:
                if not move.lot:
                    continue
                quantity = Uom.compute_qty(move.uom, move.quantity,
                    move.product.default_uom, round=False)
                outgoing_moves = outgoing_by_product[move.product.id]
                while outgoing_moves and quantity > 0:
                    out_move = outgoing_moves.pop()
                    out_quantity = Uom.compute_qty(out_move.uom,
                        out_move.quantity, out_move.product.default_uom,
                        round=False)
                    values = {}
                    if quantity < out_quantity:
                        with Transaction().set_context(_stock_move_split=True):
                            outgoing_moves.extend(
                                Move.copy([out_move], default={
                                        'quantity': out_move.uom.round(
                                            out_quantity - quantity),
                                        }))
                        values['quantity'] = out_move.uom.round(quantity)
                    values['lot'] = move.lot.id
                    to_write.extend(([out_move], values))
                    quantity -= out_quantity
                assert move.uom.round(quantity) <= 0
        if to_write:
            Move.write(*to_write)


class ShipmentOutReturn(metaclass=PoolMeta):
    __name__ = 'stock.shipment.out.return'

    @classmethod
    def _get_inventory_moves(cls, incoming_move):
        move = super(ShipmentOutReturn,
            cls)._get_inventory_moves(incoming_move)
        if move and incoming_move.lot:
            move.lot = incoming_move.lot
        return move


class Period(metaclass=PoolMeta):
    __name__ = 'stock.period'
    lot_caches = fields.One2Many('stock.period.cache.lot', 'period',
        'Lot Caches', readonly=True)

    @classmethod
    def groupings(cls):
        return super(Period, cls).groupings() + [('product', 'lot')]

    @classmethod
    def get_cache(cls, grouping):
        pool = Pool()
        Cache = super(Period, cls).get_cache(grouping)
        if grouping == ('product', 'lot'):
            return pool.get('stock.period.cache.lot')
        return Cache


class PeriodCacheLot(ModelSQL, ModelView):
    '''
    Stock Period Cache per Lot

    It is used to store cached computation of stock quantities per lot.
    '''
    __name__ = 'stock.period.cache.lot'
    period = fields.Many2One('stock.period', 'Period', required=True,
        readonly=True, select=True, ondelete='CASCADE')
    location = fields.Many2One('stock.location', 'Location', required=True,
        readonly=True, select=True, ondelete='CASCADE')
    product = fields.Many2One('product.product', 'Product', required=True,
        readonly=True, ondelete='CASCADE')
    lot = fields.Many2One('stock.lot', 'Lot', readonly=True,
        ondelete='CASCADE')
    internal_quantity = fields.Float('Internal Quantity', readonly=True)


class Inventory(metaclass=PoolMeta):
    __name__ = 'stock.inventory'

    @classmethod
    def grouping(cls):
        return super(Inventory, cls).grouping() + ('lot', )


class InventoryLine(metaclass=PoolMeta):
    __name__ = 'stock.inventory.line'
    lot = fields.Many2One('stock.lot', 'Lot',
        domain=[
            ('product', '=', Eval('product')),
            ],
        states={
            'readonly': Eval('inventory_state') != 'draft',
            },
        depends=['product', 'inventory_state'])

    @classmethod
    def __setup__(cls):
        super(InventoryLine, cls).__setup__()
        cls._order.insert(1, ('lot', 'ASC'))

    def get_rec_name(self, name):
        rec_name = super(InventoryLine, self).get_rec_name(name)
        if self.lot:
            rec_name += ' - %s' % self.lot.rec_name
        return rec_name

    def get_move(self):
        move = super(InventoryLine, self).get_move()
        if move:
            move.lot = self.lot
        return move


class InventoryCount(metaclass=PoolMeta):
    __name__ = 'stock.inventory.count'

    @classmethod
    def __setup__(cls):
        super(InventoryCount, cls).__setup__()
        cls._error_messages.update({
                'lot_required': 'Only lot can be selected for "%(product)s".',
                })

    def default_quantity(self, fields):
        pool = Pool()
        Product = pool.get('product.product')
        Inventory = pool.get('stock.inventory')
        context = Transaction().context
        inventory = Inventory(context['active_id'])
        if isinstance(self.search.search, Product):
            product = self.search.search
            if product.lot_is_required(
                    inventory.location, inventory.lost_found):
                self.raise_user_error('lot_required', {
                        'product': product.rec_name,
                        })
        return super(InventoryCount, self).default_quantity(fields)

    def get_line_domain(self, inventory):
        pool = Pool()
        Lot = pool.get('stock.lot')
        domain = super(InventoryCount, self).get_line_domain(inventory)
        if isinstance(self.search.search, Lot):
            domain.append(('lot', '=', self.search.search.id))
        return domain

    def get_line_values(self, inventory):
        pool = Pool()
        Lot = pool.get('stock.lot')
        values = super(InventoryCount, self).get_line_values(inventory)
        if isinstance(self.search.search, Lot):
            lot = self.search.search
            values['product'] = lot.product.id
            values['lot'] = lot.id
        return values


class InventoryCountSearch(metaclass=PoolMeta):
    __name__ = 'stock.inventory.count.search'

    @classmethod
    def __setup__(cls):
        super(InventoryCountSearch, cls).__setup__()
        cls.search.selection.append(('stock.lot', "Lot"))
