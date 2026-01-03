# zimra_fiscal/models/zimra_currency_mapping.py
# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import ValidationError


class ZimraCurrencyMapping(models.Model):
    _name = 'zimra.currency.mapping'
    _description = 'ZIMRA Currency Mapping'
    _rec_name = 'display_name'

    config_id = fields.Many2one('zimra.config', 'Configuration', required=True, ondelete='cascade')
    odoo_currency_id = fields.Many2one('res.currency', 'Odoo Currency', required=True)
    zimra_currency_code = fields.Char('ZIMRA Currency Code', required=True, size=3)

    display_name = fields.Char('Display Name', compute='_compute_display_name', store=True)
    is_active = fields.Boolean(string='Active', default=True)

    @api.depends('odoo_currency_id', 'zimra_currency_code')
    def _compute_display_name(self):
        for record in self:
            record.display_name = f"{record.odoo_currency_id.name} → {record.zimra_currency_code}"

    @api.constrains('zimra_currency_code')
    def _check_currency_code(self):
        for record in self:
            if len(record.zimra_currency_code) == 6:
                raise ValidationError('ZIMRA Currency Code not exceeed 5 characters')
            if not record.zimra_currency_code.isupper():
                raise ValidationError('ZIMRA Currency Code must be uppercase')

    def save_line_currencymapping(self):
        for rec in self:
            if rec.config_id:
                rec.config_id.save_currencymapping(rec)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Currency Mapping Saved',
                'message': f'Currency {rec.zimra_currency_code} mapped successfully.',
                'type': 'success',
                'sticky': False,
            }
        }

    @api.constrains('config_id', 'odoo_currency_id')
    def _check_unique_currency_mapping(self):
        for record in self:
            existing = self.search([
                ('config_id', '=', record.config_id.id),
                ('odoo_currency_id', '=', record.odoo_currency_id.id),
                ('id', '!=', record.id)
            ])
            if existing:
                raise ValidationError(
                    f'Currency mapping for {record.odoo_currency_id.name} already exists in this configuration')

    def name_get(self):
        result = []
        for record in self:
            name = f"{record.odoo_currency_id.name} → {record.zimra_currency_code}"
            result.append((record.id, name))
        return result