from trytond.pool import Pool
from .my_module import *


def register():
    Pool.register(
        MyClass,
        module='my_module', type_='model')
