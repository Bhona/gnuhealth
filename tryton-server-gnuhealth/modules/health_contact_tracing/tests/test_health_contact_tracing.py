import unittest
import trytond.tests.test_tryton
from trytond.tests.test_tryton import ModuleTestCase


class HealthContactTracingTestCase(ModuleTestCase):
    '''
    Test Health Contact Tracing module.
    '''
    module = 'health_contact_tracing'


def suite():
    suite = trytond.tests.test_tryton.suite()
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(
        HealthiContactTracingTestCase))
    return suite
