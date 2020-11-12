from trytond.pool import Pool
from .opportunity import *
from .wizard import *


def register():
    Pool.register(
        Opportunity,
        module='training', type_='model')
