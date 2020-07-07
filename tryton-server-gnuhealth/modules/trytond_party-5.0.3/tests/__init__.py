# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.

try:
    from trytond.modules.party.tests.test_party import suite
except ImportError:
    from .test_party import suite

__all__ = ['suite']
