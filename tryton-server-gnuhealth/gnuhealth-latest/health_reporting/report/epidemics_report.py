# -*- coding: utf-8 -*-
##############################################################################
#
#    GNU Health: The Free Health and Hospital Information System
#    Copyright (C) 2008-2020 Luis Falcon <falcon@gnuhealth.org>
#    Copyright (C) 2011-2020 GNU Solidario <health@gnusolidario.org>
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
from datetime import date, datetime
from trytond.report import Report
from trytond.pool import Pool
from trytond.transaction import Transaction
from dateutil.relativedelta import relativedelta
import pytz

from matplotlib import pyplot as plt
from matplotlib.ticker import MaxNLocator

from trytond.modules.health.health import convert_date_timezone

import io

__all__ = ['InstitutionEpidemicsReport']

class InstitutionEpidemicsReport(Report):
    __name__ = 'gnuhealth.epidemics.report'

    @classmethod
    def get_population_with_no_dob(cls):
        """ Return Total Number of living people in the system 
        without a date of birth"""
        cursor = Transaction().connection.cursor()

        # Check for entries without date of birth
        cursor.execute("SELECT COUNT(id) \
            FROM party_party WHERE is_person is TRUE and \
            deceased is not TRUE and dob is null")

        res = cursor.fetchone()[0]

        return(res)

    @classmethod
    def get_population(cls,date1,date2,gender,total):
        """ Return Total Number of living people in the system 
        segmented by age group and gender"""
        cursor = Transaction().connection.cursor()

        if (total):
            cursor.execute("SELECT COUNT(id) \
                FROM party_party WHERE \
                gender = %s and deceased is not TRUE",(gender))

        else:
            cursor.execute("SELECT COUNT(id) \
                FROM party_party \
                WHERE dob BETWEEN %s and %s AND \
                gender = %s  \
                and deceased is not TRUE" ,(date2, date1, gender))

        res = cursor.fetchone()[0]

        return(res)

    @classmethod
    def get_new_people(cls, start_date, end_date, in_health_system):
        """ Return Total Number of new registered persons alive """

        query = "SELECT COUNT(activation_date) \
            FROM party_party \
            WHERE activation_date BETWEEN \
            %s AND %s and is_person=True and deceased is not TRUE"
        if (in_health_system):
            query = query + " and is_patient=True"
        cursor = Transaction().connection.cursor()
        cursor.execute(query,(start_date, end_date))
        res = cursor.fetchone()
        return(res)

    @classmethod
    def get_new_births(cls, start_date, end_date):
        """ Return birth certificates within that period """

        query = "SELECT COUNT(dob) \
            FROM gnuhealth_birth_certificate \
            WHERE dob BETWEEN \
            %s AND %s"

        cursor = Transaction().connection.cursor()
        cursor.execute(query,(start_date, end_date))

        res = cursor.fetchone()
        return(res)

    @classmethod
    def get_new_deaths(cls, start_date, end_date):
        """ Return death certificates within that period """
        """ Truncate the timestamp of DoD to match a whole day"""

        query = "SELECT COUNT(dod) \
            FROM gnuhealth_death_certificate \
            WHERE date_trunc('day', dod) BETWEEN \
            %s AND %s"

        cursor = Transaction().connection.cursor() 
        cursor.execute(query,(start_date, end_date))

        res = cursor.fetchone()
        return(res)

    @classmethod
    def get_confirmed_cases(cls, start_date, end_date, dx):
        """ Return number of confirmed cases """

        Condition = Pool().get('gnuhealth.patient.disease')

        clause = [
            ('diagnosed_date', '>=', start_date),
            ('diagnosed_date', '<=', end_date),
            ]

        if dx:
            clause.append(('pathology', '=', dx))

        res = Condition.search(clause)

        return(res)

    @classmethod
    def get_epi_by_day(cls, start_date, end_date, dx):
        """ Return number of confirmed cases """

        Condition = Pool().get('gnuhealth.patient.disease')

        current_day = start_date
        aggr = []
        while current_day <= end_date:
            current_day = current_day + relativedelta(days=1)

            clause = [
                ('diagnosed_date', '=', current_day),
                ]

            if dx:
                clause.append(('pathology', '=', dx))

            res = Condition.search(clause)
            cases_day = len(res)
            daily_data = {'date':current_day, 'cases':cases_day}
            aggr.append(daily_data)
        return(aggr)


    # Death Certificates by day
    @classmethod
    def get_deaths_by_day(cls, start_date, end_date, dx):
        """ Return number of death related to the condition 
            Includes both the ultimate case as well as those
            certificates that have the condition as a leading cause
        """

        DeathCert = Pool().get('gnuhealth.death_certificate')

        current_day = start_date
        aggr = []
        while current_day <= end_date:
            cur_day_time = datetime.combine((current_day), datetime.min.time())

            utc_from =  convert_date_timezone(cur_day_time, 'utc')
            utc_to =  convert_date_timezone(cur_day_time +
                                            relativedelta(days=1), 'utc')

            clause = [
                ('dod', '>=', utc_from),
                ('dod', '<', utc_to)
                ]

            res = DeathCert.search(clause)

            # Reset the cases for each day
            as_immediate_cause = 0
            as_underlying_condition = 0

            # Get the immediate cause of death and the underlying conditions
            # for each certificate on each day.

            for cert in res:
                if (cert.cod.id == dx):
                    as_immediate_cause = as_immediate_cause + 1
                for underlying_condition in cert.underlying_conditions:
                    if (underlying_condition.condition.id == dx):
                        as_underlying_condition = as_underlying_condition + 1

            daily_data = {'date':current_day,
                          'certs_day_ic':as_immediate_cause,
                          'certs_day_uc':as_underlying_condition}
            aggr.append(daily_data)
            current_day = current_day + relativedelta(days=1)

        return(aggr)


    @classmethod
    def plot_cases_timeseries(cls, start_date, end_date,
                             health_condition_id, hc):

        epi_series = cls.get_epi_by_day(start_date,
                                        end_date, health_condition_id)

        days=[]
        cases_day=[]
        for day in epi_series:
            days.append(day['date'])
            # Confirmed cases by day
            cases_day.append(day['cases'])

        fig = plt.figure(figsize=(6,3))
        cases_by_day = fig.add_subplot(1, 1, 1)
        title = 'New cases by day: ' + hc.rec_name
        cases_by_day.set_title(title)
        cases_by_day.bar(days,cases_day)
        cases_by_day.yaxis.set_major_locator(MaxNLocator(integer=True))
        fig.autofmt_xdate()

        holder = io.BytesIO()
        fig.savefig(holder, format="svg")
        image_png = holder.getvalue()

        holder.close()
        return (image_png)


    @classmethod
    def plot_deaths_timeseries(cls, start_date,
                              end_date, health_condition_id, hc):

        death_certs = cls.get_deaths_by_day(start_date,
                                            end_date, health_condition_id)

        days=[]
        certs_ic_day=[]
        certs_uc_day=[]
        for day in death_certs:
            days.append(day['date'])
            # Death certificates as an immediate cause
            certs_ic_day.append(day['certs_day_ic'])
            # Death certificates as an underlying cause
            certs_uc_day.append(day['certs_day_uc'])

        title = "New deaths by day: " + hc.rec_name
        fig = plt.figure(figsize=(6,3))
        deaths_by_day = fig.add_subplot(1, 1, 1)
        deaths_by_day.set_title(title)
        deaths_by_day.plot(days,certs_ic_day, label="immediate cause")
        deaths_by_day.plot(days,certs_uc_day, label="underlying condition")
        deaths_by_day.yaxis.set_major_locator(MaxNLocator(integer=True))
        deaths_by_day.legend()

        fig.autofmt_xdate()

        holder = io.BytesIO()
        fig.savefig(holder, format="svg")
        image_png = holder.getvalue()

        holder.close()
        return (image_png)


    @classmethod
    def plot_cases_ethnicity(cls,start_date,end_date,ethnic_count, hc):

        for k, v in list(ethnic_count.items()):
            if (v==0):
                # Remove ethnicities with zero cases from the plot
                del(ethnic_count[k])

        title = "Cases by ethnic group: " + hc.rec_name
        fig = plt.figure(figsize=(6,3))
        cases_by_ethnicity = fig.add_subplot(1, 1, 1)
        cases_by_ethnicity.set_title(title)
        cases_by_ethnicity.pie(ethnic_count.values(),
                               autopct='%1.1f%%',
                               labels=ethnic_count.keys())

        fig.autofmt_xdate()

        holder = io.BytesIO()
        fig.savefig(holder, format="svg")
        image_png = holder.getvalue()

        holder.close()
        return (image_png)


    @classmethod
    def get_ethnic_groups(cls):
        #Build a list with the ethnic groups
        Condition = Pool().get('gnuhealth.ethnicity')
        ethnic_groups = Condition.search([])
        ethnicities = []
        for ethnic_group in ethnic_groups:
            ethnicities.append(ethnic_group.name)
        return (ethnicities)


    @classmethod
    def plot_cases_socioeconomics(cls,start_date,end_date,ses_count, hc):

        for k, v in list(ses_count.items()):
            if (v==0):
                # Remove socioeconomic groups with zero cases from the plot
                del(ses_count[k])

        title = "Cases by Socioeconomic groups: " + hc.rec_name
        fig = plt.figure(figsize=(6,3))
        cases_by_socioeconomics = fig.add_subplot(1, 1, 1)
        cases_by_socioeconomics.set_title(title)
        cases_by_socioeconomics.pie(ses_count.values(),
                               autopct='%1.1f%%',
                               labels=ses_count.keys())

        fig.autofmt_xdate()

        holder = io.BytesIO()
        fig.savefig(holder, format="svg")
        image_png = holder.getvalue()

        holder.close()
        return (image_png)

    @classmethod
    def get_context(cls, records, data):

        Condition = Pool().get('gnuhealth.pathology')

        ethnic_groups = cls.get_ethnic_groups()

        ethnic_count = {}
        for ethnic_group in ethnic_groups:
            ethnic_count[ethnic_group] = 0

        ses_count = {}

        ses_groups = ['lower','lower-middle','middle','upper-middle','upper']
        for ses_group in ses_groups:
            ses_count[ses_group] = 0

        context = super(InstitutionEpidemicsReport, cls).get_context(records,
                                                                     data)

        start_date = data['start_date']
        context['start_date'] = data['start_date']

        end_date = data['end_date']
        context['end_date'] = data['end_date']

        demographics = data['demographics']
        context['demographics'] = data['demographics']

        health_condition_id = data['health_condition']

        hc = Condition.search(
                [('id', '=', health_condition_id)], limit=1)[0]

        context['health_condition'] = hc

        # Demographics
        today = date.today()

        context[''.join(['p','total_','f'])] = \
            cls.get_population (None,None,'f', total=True)

        context[''.join(['p','total_','m'])] = \
            cls.get_population (None,None,'m', total=True)

        # Living people with NO date of birth
        context['no_dob'] = \
            cls.get_population_with_no_dob()

        # Build the Population Pyramid for registered people

        for age_group in range (0,21):
            date1 = today - relativedelta(years=(age_group*5))
            date2 = today - relativedelta(years=((age_group*5)+5), days=-1)

            context[''.join(['p',str(age_group),'f'])] = \
                cls.get_population (date1,date2,'f', total=False)
            context[''.join(['p',str(age_group),'m'])] = \
                cls.get_population (date1,date2,'m', total=False)


        # Count those lucky over 105 years old :)
        date1 = today - relativedelta(years=105)
        date2 = today - relativedelta(years=200)

        context['over105f'] = \
            cls.get_population (date1,date2,'f', total=False)
        context['over105m'] = \
            cls.get_population (date1,date2,'m', total=False)


        # Count registered people, and those within the system of health
        context['new_people'] = \
            cls.get_new_people(start_date, end_date, False)
        context['new_in_health_system'] = \
            cls.get_new_people(start_date, end_date, in_health_system=True)

        # New births
        context['new_births'] = \
            cls.get_new_births(start_date, end_date)

        # New deaths
        context['new_deaths'] = \
            cls.get_new_deaths(start_date, end_date)

        # Get cases within the specified date range

        confirmed_cases = cls.get_confirmed_cases(start_date, end_date,
                                                  health_condition_id)

        context['confirmed_cases'] = confirmed_cases

        epidemics_dx =[]
        non_age_cases = 0
        cases_f = 0
        cases_m = 0


        # Global Condition info
        for confirmed_case in confirmed_cases:
            #Sex distribution
            if (confirmed_case.name.gender == 'f'):
                cases_f +=1
            else:
                cases_m +=1

            #Ethnic groups distribution
            if (confirmed_case.name.name.ethnic_group):
                ethnicity = confirmed_case.name.name.ethnic_group.name
                if (ethnicity in ethnic_groups):
                    ethnic_count[ethnicity] =  ethnic_count[ethnicity] + 1

            #Socioeconomic groups distribution
            if (confirmed_case.name.ses):
                ses_id = confirmed_case.name.ses
                if (ses_id == '0'):
                    ses_count['lower'] +=1
                if (ses_id == '1'):
                    ses_count['lower-middle'] +=1
                if (ses_id == '2'):
                    ses_count['middle'] +=1
                if (ses_id == '3'):
                    ses_count['upper-middle'] +=1
                if (ses_id == '4'):
                    ses_count['upper'] +=1

            if not confirmed_case.name.age:
                non_age_cases +=1

        total_cases = len(confirmed_cases)

        context['confirmed_cases_num'] = total_cases
        context['cases_f'] = cases_f
        context['cases_m'] = cases_m
        context['non_age_cases'] = non_age_cases

        group_1 = group_2 = group_3 = group_4 = group_5 = 0
        group_1f = group_2f = group_3f = group_4f = group_5f = 0


        for case in confirmed_cases:

            if (case.name.age):

                #Strip to get the raw year
                age = int(case.name.age.split(' ')[0][:-1])


                # Age groups in this diagnostic
                if (age < 5):
                    group_1 += 1
                    if (case.name.gender == 'f'):
                        group_1f += 1
                if (age in range(5,14)):
                    group_2 += 1
                    if (case.name.gender == 'f'):
                        group_2f += 1
                if (age in range(15,45)):
                    group_3 += 1
                    if (case.name.gender == 'f'):
                        group_3f += 1
                if (age in range(46,60)):
                    group_4 += 1
                    if (case.name.gender == 'f'):
                        group_4f += 1
                if (age > 60):
                    group_5 += 1
                    if (case.name.gender == 'f'):
                        group_5f += 1

        cases = {'diagnosis': health_condition_id,
            'age_group_1': group_1, 'age_group_1f': group_1f,
            'age_group_2': group_2, 'age_group_2f': group_2f,
            'age_group_3': group_3, 'age_group_3f': group_3f,
            'age_group_4': group_4, 'age_group_4f': group_4f,
            'age_group_5': group_5, 'age_group_5f': group_5f,
            'total': total_cases,}

        # Append into the report list the resulting
        # dictionary entry

        epidemics_dx.append(cases)

        context['epidemics_dx'] = epidemics_dx

        # New cases by day
        context['cases_timeseries'] = cls.plot_cases_timeseries(start_date,
                                                end_date,
                                                health_condition_id, hc)

        #Cases by ethnic groups
        context['cases_ethnicity'] = cls.plot_cases_ethnicity(start_date,
                                                end_date,ethnic_count, hc)

        #Cases by Socioeconomic groups
        context['cases_ses'] = cls.plot_cases_socioeconomics(start_date,
                                                end_date,ses_count, hc)

        # Death certificates by day
        context['deaths_timeseries'] = cls.plot_deaths_timeseries(start_date,
                                                end_date,
                                                health_condition_id, hc)

        return context
