# -*- coding: utf-8 -*-

from trytond.model import ModelView, ModelSQL, fields

__all__ = ['MyClass']


class MyClass(ModelSQL,ModelView):
    
    'My first module'

    __name__ = 'mymodule.myclass'
    
    name = fields.Char('Name', required=True)
    
    lastname = fields.Char('Surname', required=True)    
    
    pid = fields.Char('PID', required=True)    
    
    dob = fields.Date('DoB', help='Date of Birth', required=True)
    
    sex = fields.Selection([
        (None , ''),
        ('f', 'Female'),
        ('m', 'Male'),
        ], 'Sex', required=True, sort=False)
    
    weight = fields.Float('Weight', digits=(3,2),help='Weight in kilos')
    
    height = fields.Float('Height', digits=(3,1), help='Height in centimeters')
    
    bmi = fields.Function(fields.Float('BMI', digits=(2,2),help='Body mass index'), 'get_bmi')
    
    donate = fields.Boolean('Donate')    
    
    country  = fields.Char('Country')    
    
    notes = fields.Text('Notes')


    @staticmethod
    def default_country():
        return 'Bolivia'
    
    
    def get_bmi():
        
        bmi = 00.00
        bmi = weigth / pow((height/100),2)
        return get_bmi
    
    
    

    


