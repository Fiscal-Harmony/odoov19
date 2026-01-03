# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)


class ZimraTaxMapping(models.Model):
    _name = 'zimra.tax.mapping'
    _description = 'ZIMRA Tax Mapping'
    _rec_name = 'display_name'

    config_id = fields.Many2one('zimra.config', 'Configuration', required=True, ondelete='cascade')
    odoo_tax_id = fields.Many2one('account.tax', 'Odoo Tax')
    zimra_tax_code = fields.Char('ZIMRA Tax Code', required=True)
    zimra_tax_name = fields.Char('ZIMRA Tax Name', required=True)
    zimra_tax_rate = fields.Float('ZIMRA Tax Rate (%)', required=True)
    zimra_tax_type = fields.Selection([
        ('Exempt', 'Exempt'),
        ('Standard rated 15%', 'Standard rated 15%'),
        ('Zero rated 0%', 'Zero rated 0%'),
        ('Non-VAT Withholding Tax', 'Non-VAT Withholding Tax')
    ], string='Tax Type', required=True, default='Exempt')

    # Additional fields from device response
    tax_description = fields.Text('Tax Description')
    is_active = fields.Boolean('Active', default=True)

    display_name = fields.Char('Display Name', compute='_compute_display_name', store=True)

    @api.depends('odoo_tax_id', 'zimra_tax_code')
    def _compute_display_name(self):
        for record in self:
            tax_name = record.odoo_tax_id.name if record.odoo_tax_id else 'No Tax'
            record.display_name = f"{tax_name} → {record.zimra_tax_code}"

    def save_line_taxmapping(self):
        for rec in self:
            if rec.config_id:
                rec.config_id.save_taxmapping(rec)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Tax Mapping Saved',
                'message': f'Tax {rec.zimra_tax_type} saved successfully.',
                'type': 'success',
                'sticky': False,
            }
        }

    @api.constrains('zimra_tax_rate')
    def _check_tax_rate(self):
        for record in self:
            if record.zimra_tax_rate < 0 or record.zimra_tax_rate > 100:
                raise ValidationError('Tax rate must be between 0 and 100%')

    @api.constrains('config_id', 'odoo_tax_id')
    def _check_unique_tax_mapping(self):
        for record in self:
            if not record.odoo_tax_id:  # Skip check if no tax is assigned
                continue

            existing = self.search([
                ('config_id', '=', record.config_id.id),
                ('odoo_tax_id', '=', record.odoo_tax_id.id),
                ('id', '!=', record.id)
            ])
            if existing:
                raise ValidationError(f'Tax mapping already exists for this Odoo tax in this configuration')

    def name_get(self):
        result = []
        for record in self:
            tax_name = record.odoo_tax_id.name if record.odoo_tax_id else 'No Tax'
            name = f"{tax_name} → {record.zimra_tax_code} ({record.zimra_tax_rate}%)"
            result.append((record.id, name))
        return result

    def write(self, vals):
        result = super().write(vals)
        # Don't auto-sync during write to avoid errors
        # Users can manually sync using the save_line_taxmapping button
        return result

    @api.model
    def create(self, vals_list):
        """Override create to handle both single dict and list of dicts (Odoo 19+)."""
        # In Odoo 19+, create() receives a list of dictionaries
        # But we need to handle the case where vals_list might be a single dict for compatibility

        # Normalize input to always be a list
        if isinstance(vals_list, dict):
            vals_list = [vals_list]

        # Call super with the list
        records = super(ZimraTaxMapping, self).create(vals_list)

        # Don't auto-sync during creation to avoid errors
        # Users can manually sync using the save_line_taxmapping button

        return records

    @api.onchange('zimra_tax_type')
    def _onchange_zimra_tax_type(self):
        # Updated to match the actual API response tax IDs and rates
        tax_lookup = {
            'Exempt': {'taxID': 3, 'taxName': 'Exempt', 'rate': 0.0, 'code': 3},
            'Zero rated 0%': {'taxID': 2, 'taxName': 'Zero rated 0%', 'rate': 0.0, 'code': 2},
            'Standard rated 15%': {'taxID': 1, 'taxName': 'Standard rated 15%', 'rate': 15.0, 'code': 1},
            'Non-VAT Withholding Tax': {'taxID': 514, 'taxName': 'Non-VAT Withholding Tax', 'rate': 5.0, 'code': 514},
        }

        for rec in self:
            selected = tax_lookup.get(rec.zimra_tax_type)
            if selected:
                rec.zimra_tax_code = selected['code']
                rec.zimra_tax_name = selected['taxName']
                rec.zimra_tax_rate = selected['rate']
                rec.tax_description = f"Auto-filled: {selected['taxName']} ({selected['rate']}%)"

    @api.model
    def get_valid_tax_types(self):
        """Return all valid tax type selection values"""
        return [value[0] for value in self._fields['zimra_tax_type'].selection]

    @api.model
    def normalize_tax_type(self, api_tax_name):
        """Normalize API tax name to valid selection value"""
        mapping = {
            # Standard variations
            'Standard rated 15%': 'Standard rated 15%',
            'Standard rated 15.5%': 'Standard rated 15%',  # Added 15.5% mapping
            'Standard rate 15%': 'Standard rated 15%',
            'Standard rate 15.5%': 'Standard rated 15%',

            # Zero rate variations
            'Zero rate 0%': 'Zero rated 0%',
            'Zero rated 0%': 'Zero rated 0%',
            'Zero rate': 'Zero rated 0%',
            'Zero rated': 'Zero rated 0%',

            # Exempt variations
            'Exempt': 'Exempt',
            'Tax Exempt': 'Exempt',
            'Exempted': 'Exempt',

            # Withholding variations
            'Non-VAT Withholding Tax': 'Non-VAT Withholding Tax',
            'Withholding Tax': 'Non-VAT Withholding Tax',
            'Non VAT Withholding Tax': 'Non-VAT Withholding Tax',
        }

        # Try exact match first
        normalized = mapping.get(api_tax_name)
        if normalized:
            return normalized

        # Try case-insensitive match
        api_name_lower = api_tax_name.lower().strip()
        for key, value in mapping.items():
            if key.lower().strip() == api_name_lower:
                return value

        # Pattern matching fallback
        if '15' in api_name_lower or 'standard' in api_name_lower:
            return 'Standard rated 15%'
        elif 'zero' in api_name_lower or '0%' in api_name_lower:
            return 'Zero rated 0%'
        elif 'exempt' in api_name_lower:
            return 'Exempt'
        elif 'withholding' in api_name_lower:
            return 'Non-VAT Withholding Tax'

        # Default fallback
        return 'Exempt'