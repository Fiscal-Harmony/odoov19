# -*- coding: utf-8 -*-
from datetime import timedelta
from odoo import models, fields, api
import json

from odoo.exceptions import UserError


class ZimraInvoice(models.Model):
    _name = 'zimra.invoice'
    _description = 'ZIMRA Invoice Log'
    _order = 'create_date desc'
    _rec_name = 'name'
    _inherit = ['mail.thread', 'mail.activity.mixin']  # ✅ Enable chatter

    name = fields.Char('Invoice Number', required=True, tracking=True)
    pos_order_id = fields.Many2one('pos.order', 'POS Order', ondelete='cascade', tracking=True)
    account_move_id = fields.Many2one('account.move', 'Account Move', ondelete='cascade', tracking=True)
    zimra_fiscal_number = fields.Char('ZIMRA Fiscal Number', tracking=True)

    status = fields.Selection([
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('fiscalized', 'Fiscalized'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled')
    ], string='Status', default='pending', tracking=True)

    # Request/Response Data
    request_data = fields.Text('Request Data')
    response_data = fields.Text('Response Data')
    error_message = fields.Text('Error Message')

    # Timestamps
    sent_date = fields.Datetime('Sent Date', tracking=True)
    fiscalized_date = fields.Datetime('Fiscalized Date', tracking=True)

    # Company
    company_id = fields.Many2one(
        'res.company',
        'Company',
        required=True,
        default=lambda self: self.env.company,
        tracking=True
    )

    # Additional fields
    retry_count = fields.Integer('Retry Count', default=0, tracking=True)
    duration = fields.Float('Duration (seconds)', help='Time taken to process the request')


    # ========== ACTIONS ==========

    def action_view_pos_order(self):
        """View related POS order"""
        self.ensure_one()
        if self.pos_order_id:
            return {
                'type': 'ir.actions.act_window',
                'name': 'POS Order',
                'res_model': 'pos.order',
                'res_id': self.pos_order_id.id,
                'view_mode': 'form',
                'target': 'current',
            }

    def action_view_related_document(self):
        """View related POS order or invoice"""
        self.ensure_one()
        if self.pos_order_id:
            return {
                'type': 'ir.actions.act_window',
                'name': 'POS Order',
                'res_model': 'pos.order',
                'res_id': self.pos_order_id.id,
                'view_mode': 'form',
            }
        elif self.account_move_id:
            return {
                'type': 'ir.actions.act_window',
                'name': 'Invoice',
                'res_model': 'account.move',
                'res_id': self.account_move_id.id,
                'view_mode': 'form',
            }

    show_view_invoice = fields.Boolean(string="Can View Invoice", compute="_compute_show_view_invoice")

    @api.depends('status')
    def _compute_show_view_invoice(self):
        for record in self:
            record.show_view_invoice = record.status == 'fiscalized'

    def open_downloaded_invoice(self):
        """Open the form view of the selected invoice using the PDF name from POS order"""
        self.ensure_one()
        config = self.env['zimra.config'].search([], limit=1)

        # Ensure pos_order_id is set and has the fiscalized_pdf field
        if not self.pos_order_id or not self.pos_order_id.fiscalized_pdf:
            raise UserError("Fiscalized PDF is not available for this order.")

        # Get the PDF name or ID from the POS Order
        pdf_name = self.pos_order_id.fiscalized_pdf

        # Call the config to download the PDF
        response = config.download_pdf(pdf_name)
        pdffile = response  # Base64 encoded PDF

        # Store PDF temporarily in attachment
        attachment = self.env['ir.attachment'].create({
            'name': f'{pdf_name}.pdf',
            'type': 'binary',
            'datas': pdffile,
            'res_model': 'zimra.invoice',
            'res_id': self.id,
            'mimetype': 'application/pdf',
        })

        # Return URL action to open PDF directly
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'new',
        }

    def action_retry_fiscalization(self):
        """Retry fiscalization for both POS orders and accounting invoices"""
        self.ensure_one()

        if self.status not in ['failed', 'cancelled']:
            raise UserError("Can only retry failed or cancelled fiscalizations.")

        # Update status and retry count
        self.message_post(body="Fiscalization retry initiated by user.")
        self.write({
            'status': 'pending',
            'retry_count': self.retry_count + 1,
            'error_message': False,
        })

        try:
            # Handle POS Order retry
            if self.pos_order_id:
                if not self.pos_order_id.exists():
                    raise UserError("Related POS order no longer exists.")
                return self.pos_order_id._send_to_zimra()

            # Handle Account Move (Invoice) retry
            elif self.account_move_id:
                if not self.account_move_id.exists():
                    raise UserError("Related invoice no longer exists.")

                # Check if invoice is in valid state for fiscalization
                if self.account_move_id.state != 'posted':
                    raise UserError("Invoice must be posted to retry fiscalization.")

                # Check if the method exists on account.move
                if not hasattr(self.account_move_id, '_send_to_zimra'):
                    raise UserError("Fiscalization method not implemented for invoices.")

                return self.account_move_id._send_to_zimra()

            else:
                raise UserError("No related POS order or invoice found for this ZIMRA record.")

        except Exception as e:
            # Revert status on error
            self.write({
                'status': 'failed',
                'error_message': str(e),
            })
            self.message_post(body=f"Retry failed: {str(e)}")
            raise

        return False

    def action_cancel_fiscalization(self):
        """Cancel fiscalization"""
        self.ensure_one()
        if self.status in ['pending', 'sent', 'failed']:
            self.write({
                'status': 'cancelled',
                'error_message': 'Fiscalization cancelled by user',
            })
            self.message_post(body="Fiscalization manually cancelled.")


    # ========== HELPERS ==========

    def get_request_data_json(self):
        """Get request data as JSON object"""
        self.ensure_one()
        try:
            return json.loads(self.request_data or '{}')
        except json.JSONDecodeError:
            return {}

    def get_response_data_json(self):
        """Get response data as JSON object"""
        self.ensure_one()
        try:
            return json.loads(self.response_data or '{}')
        except json.JSONDecodeError:
            return {}

    @api.model
    def cleanup_old_records(self, days=90):
        """Clean up old fiscalization records"""
        cutoff_date = fields.Datetime.now() - timedelta(days=days)
        old_records = self.search([
            ('create_date', '<', cutoff_date),
            ('status', 'in', ['fiscalized', 'cancelled'])
        ])
        return old_records.unlink()

    def name_get(self):
        """Custom name display in many2one fields"""
        result = []
        for record in self:
            name = f"{record.name} [{record.status}]"
            if record.zimra_fiscal_number:
                name += f" - {record.zimra_fiscal_number}"
            result.append((record.id, name))
        return result
    def action_view_pos_orders(self):
        """View POS orders for this configuration"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'POS Orders',
            'res_model': 'pos.order',
            'view_mode': 'tree,form',
            'domain': [('company_id', '=', self.company_id.id)],
            'context': {'default_company_id': self.company_id.id}
        }

    def action_view_failed_orders(self):
        """View failed POS orders"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Failed Orders',
            'res_model': 'pos.order',
            'view_mode': 'tree,form',
            'domain': [
                ('company_id', '=', self.company_id.id),
                ('zimra_status', '=', 'failed')
            ],
            'context': {'default_company_id': self.company_id.id}
        }

    total_sent = fields.Integer('Total Sent', compute='_compute_statistics')
    total_fiscalized = fields.Integer('Total Fiscalized', compute='_compute_statistics')
    total_failed = fields.Integer('Total Failed', compute='_compute_statistics')
    @api.depends('company_id')
    def _compute_statistics(self):
        for record in self:
            domain = [('company_id', '=', record.company_id.id)]

            record.total_sent = self.env['pos.order'].search_count(
                domain + [('zimra_status', 'in', ['sent', 'fiscalized'])]
            )
            record.total_fiscalized = self.env['pos.order'].search_count(
                domain + [('zimra_status', '=', 'fiscalized')]
            )
            record.total_failed = self.env['pos.order'].search_count(
                domain + [('zimra_status', '=', 'failed')]
            )