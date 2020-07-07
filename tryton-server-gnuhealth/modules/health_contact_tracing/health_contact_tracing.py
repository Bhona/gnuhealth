# -*- coding: utf-8 -*-
##############################################################################
#
#    GNU Health: The Free Health and Hospital Information System
#    Copyright (C) 2008-2020 Luis Falcon <lfalcon@gnusolidario.org>
#    Copyright (C) 2011-2020 GNU Solidario <health@gnusolidario.org>
#
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
from trytond.model import ModelView, ModelSQL, fields, Unique
from trytond.pool import Pool


__all__ = ['ContactTracing']

class ContactTracing(ModelSQL, ModelView):
    'Person Contacts tracing'
    __name__ = 'gnuhealth.contact_tracing'

    patient = fields.Many2One(
        'gnuhealth.patient', 'Source', required=True,
        help="Person suspected or confirmed")

    pathology = fields.Many2One(
        'gnuhealth.pathology', 'Disease',
        help='Disease to trace')

    contact = fields.Many2One('party.party','Contact',
        domain=[('is_person', '=', True)],
        help="Person that the patient has contacted")

    contact_date = fields.DateTime("Date")

    du = fields.Many2One(
        'gnuhealth.du', 'Dom.Unit',
        help="Domiciliary Unit")

    operational_sector = fields.Many2One(
        'gnuhealth.operational_sector', 'Op.Sector',
        help="Operational / Sanitary region")

    exposure_risk = fields.Selection((
        ('low', 'Low'),
        ('high', 'High'),
        ('na', 'Not available'),
        ), 'Exposure', required=True, sort=False)

    exposure_time = fields.Integer("Exposure time",
                    help="Exposure time in minutes")

    status = fields.Selection((
        ('unreached', 'Unreached'),
        ('followingup', 'Following up'),
        ('na', 'Not available'),
        ), 'Status', required=True, sort=False,
        help="Unreached: The contact has not been reached yet."
        "\nFollowing up: The contact has been traced, demographics information"
        " has been created and followup evaluations status are stored in the"
        " evaluations")

    context = fields.Char ("Context",
                           help="Airport, meeting, concert, ...")

    comments = fields.Text ("Comments")


    @fields.depends('du')
    def on_change_with_operational_sector(self):
        if (self.du and self.du.operational_sector):
            return (self.du.operational_sector.id)
