from trytond.model import ModelSQL, ModelView, fields

__all__ = ['Opportunity']

class Opportunity(ModelSQL, ModelView):
    'Opportunity'
    __name__ = 'trainning.opportunity'

    description = fields.Char('Description', required = True)