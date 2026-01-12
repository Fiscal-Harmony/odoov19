# -*- coding: utf-8 -*-
from odoo import models, fields, api
import json
import requests
import logging
import re
from datetime import datetime

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = 'pos.order'

    #  Fiscal INVOICE ZIMRA Status Fields
    zimra_status = fields.Selection([
        ('pending', 'Pending'),
        ('all', 'all'),
        ('sent', 'Sent'),
        ('fiscalized', 'Fiscalized'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
        ('exempted', 'Exempted')
    ], string=' Status', default='pending', tracking=True)

    zimra_fiscal_number = fields.Char('ZIMRA Status number', readonly=True, copy=False)
    zimra_response = fields.Text('FiscalHarmony Response', readonly=True, copy=False)
    zimra_error = fields.Text('FiscalHarmony Error', readonly=True, copy=False)
    zimra_sent_date = fields.Datetime(' Sent Date', readonly=True, copy=False)
    zimra_fiscalized_date = fields.Datetime(' Fiscalized Date', readonly=True, copy=False)
    zimra_retry_count = fields.Integer('Retry Count', default=0, copy=False)

    # Additional ZIMRA fields
    zimra_qr_code = fields.Char(' QR Data', readonly=True, copy=False)
    fiscalized_pdf = fields.Char('Fiscalized Pdf', readonly=True, copy=False)
    zimra_verification_url = fields.Char('ZIMRA Verification URL', readonly=True, copy=False)
    zimra_attempted = fields.Boolean(
        string="ZIMRA Attempted",
        default=False,
        copy=False
    )

    # Add field to store PDF attachment ID
    fiscal_pdf_attachment_id = fields.Many2one('ir.attachment', 'Fiscal PDF', readonly=True, copy=False)

    def action_fiscalize_manual(self):
        """Manual fiscalization action"""
        self.ensure_one()
        if self.zimra_status in ['fiscalized', 'sent']:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Already Fiscalized',
                    'message': 'This order has already been fiscalized',
                    'type': 'warning',
                }
            }

        result = self._send_to_zimra()

        if result:
            message = f'Order {self.name} has been successfully fiscalized'
            if self.fiscal_pdf_attachment_id:
                pdf_url = f'/web/content/{self.fiscal_pdf_attachment_id.id}?filename=FiscalInvoice.pdf'
                message += f'. <a href="{pdf_url}" target="_blank" class="btn btn-primary btn-sm">View PDF</a>'

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Fiscalization Successful',
                    'message': message,
                    'type': 'success',
                    'sticky': bool(self.fiscal_pdf_attachment_id),
                }
            }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Fiscalization Failed',
                    'message': f'Failed to fiscalize order {self.name}. Check error details.',
                    'type': 'danger',
                }
            }

    def _send_to_zimra(self):
        """Send invoice to ZIMRA using signed request from config"""
        self.ensure_one()
        

        # Skip if name is still the placeholder '/'
        if not self.name or self.name == '/':
            if hasattr(self.config_id, 'sequence_id') and self.config_id.sequence_id:
                # Assign sequence now
                self.name = self.config_id.sequence_id.next_by_id()
                _logger.info("Assigned sequence %s to POS order %s", self.name, self.id)
            else:
                _logger.warning(
                    "POS config %s has no sequence. Skipping fiscalization for order %s",
                    getattr(self.config_id, 'name', 'Unknown'), self.id
                )
                return False

                # Check if this invoice ID has already been fiscalized
        existing_fiscalized = self.search([
            ('name', '=', self.name),
            ('zimra_status', '=', 'fiscalized'),
            ('id', '!=', self.id)
        ], limit=1)

        if existing_fiscalized:
            self.zimra_status = 'exempted'
            self.zimra_error = f'Invoice {self.name} already fiscalized in order {existing_fiscalized.id}'
            _logger.warning(f"Skipping fiscalization - Invoice {self.name} already fiscalized")
            return True

        # Get configuration
        warehouse = self.session_id.config_id.picking_type_id.warehouse_id
        config = self.env['zimra.config'].get_active_config(warehouse.id)
        _logger.info("zimra says warehouse is:%s", warehouse)

        if not config:
            self.zimra_status = 'failed'
            self.zimra_error = 'No active FiscalHarmony configuration found'
            _logger.error(f"No ZIMRA configuration found for company {self.company_id.name}")
            return False

        # Check if order should be fiscalized
        if not self._should_fiscalize():
            self.zimra_status = 'exempted'
            return True

        try:
            # Prepare ZIMRA invoice data
            invoice_data = self._prepare_zimra_invoice_data(config)

            # Log the invoice
            zimra_invoice = self.env['zimra.invoice'].create({
                'name': self.name,
                'pos_order_id': self.id,
                'status': 'pending',
                'request_data': json.dumps(invoice_data, indent=2),
                'company_id': self.company_id.id,
            })

            # Update fields before sending
            self.zimra_sent_date = fields.Datetime.now()
            self.zimra_retry_count += 1

            # Update invoice log
            zimra_invoice.write({
                'status': 'sent',
                'sent_date': self.zimra_sent_date,
            })

            fiscal_invoice = json.dumps(invoice_data, separators=(',', ':'), ensure_ascii=False)

            invoice_id = invoice_data.get("InvoiceId", "").strip().lower()

            # Check for CreditNoteId first
            if "CreditNoteId" in invoice_data and invoice_data["CreditNoteId"]:
                endpoint = "/creditnote"
            # Fallback: check if 'refund' is in the invoice ID
            elif "refund" in invoice_id:
                endpoint = "/creditnote"
            else:
                endpoint = "/invoice"
            # Use the signed request method from config
            response_data = config.send_fiscal_data(fiscal_invoice, endpoint)
            _logger.info("zimra says:%s", response_data)

            # Store the response
            self.zimra_response = json.dumps(response_data) if response_data else ''

            # Update invoice log
            zimra_invoice.write({
                'response_data': self.zimra_response,
            })

            # Check if fiscalization was successful
            if self._is_fiscalization_successful(response_data):
                # response_data is a list, so get the first element
                response = response_data[0] if response_data else {}
                fiscalday = response.get("FiscalDay")
                invoice_number = response.get("InvoiceNumber")

                self.zimra_status = 'fiscalized'
                self.zimra_fiscal_number = f"{invoice_number}/{fiscalday}"
                self.zimra_fiscalized_date = fields.Datetime.now()
                self.zimra_qr_code = response.get('QrData')
                self.fiscalized_pdf = response.get('FiscalInvoicePdf')
                self.zimra_verification_url = response.get('verification_url')

                # Clear any previous errors
                self.zimra_error = False

                # Update invoice log
                zimra_invoice.write({
                    'status': 'fiscalized',
                    'zimra_fiscal_number': f"{invoice_number}/{fiscalday}",
                    'fiscalized_date': self.zimra_fiscalized_date,
                })

                _logger.info(
                    f"Successfully fiscalized POS order {self.name} - Fiscal Number: {self.zimra_fiscal_number}")

                # AUTO-DOWNLOAD PDF AFTER SUCCESSFUL FISCALIZATION
                if self.fiscalized_pdf:
                    try:
                        _logger.info(f"Attempting to auto-download PDF for order {self.name}")
                        pdf_data = config.download_pdf(self.fiscalized_pdf)

                        if isinstance(pdf_data, str):
                            attachment_vals = {
                                'name': f'FiscalInvoice_{self.name}.pdf',
                                'type': 'binary',
                                'datas': pdf_data,
                                'res_model': 'pos.order',
                                'res_id': self.id,
                                'mimetype': 'application/pdf',
                            }

                            if self.fiscal_pdf_attachment_id:
                                self.fiscal_pdf_attachment_id.write(attachment_vals)
                            else:
                                attachment = self.env['ir.attachment'].create(attachment_vals)
                                self.fiscal_pdf_attachment_id = attachment.id

                            _logger.info(f"Successfully auto-downloaded and stored PDF for order {self.name}")
                        else:
                            _logger.warning(
                                f"Failed to auto-download PDF for order {self.name}. Status code: {pdf_data}")

                    except Exception as pdf_error:
                        _logger.error(f"Error auto-downloading PDF for order {self.name}: {str(pdf_error)}")
                        # Don't fail the entire fiscalization process if PDF download fails

                return True

            else:
                # response_data is a list, so get the first element
                response = response_data[0] if response_data else {}

                self.zimra_status = 'failed'
                self.zimra_fiscal_number = response.get('fiscal_number', response.get('RequestId'))
                self.zimra_error = response.get('Error')

                # Update invoice log
                zimra_invoice.write({
                    'status': 'failed',
                    'error_message': self.zimra_error,
                    'zimra_fiscal_number': self.zimra_fiscal_number,
                })

                _logger.error(
                    f"Failed to fiscalize POS order {self.name} - Error: {self.zimra_error}")
                return False

        except Exception as e:
            error_msg = str(e)
            self.zimra_status = 'failed'
            self.zimra_error = error_msg

            # Update invoice log if it exists
            if 'zimra_invoice' in locals():
                zimra_invoice.write({
                    'status': 'failed',
                    'error_message': error_msg,
                })

            _logger.error(f"Error fiscalizing POS order {self.name}: {error_msg}")
            return False

    def _is_fiscalization_successful(self, response_data):
        """Check if fiscalization response indicates success based on 'Error' field."""
        if not response_data or not isinstance(response_data, list):
            return False

        response = response_data[0]
        return not response.get("Error")  # True if Error is None or ''

    def action_retry_fiscalization(self):
        """Retry fiscalization for failed orders"""
        self.ensure_one()
        if self.zimra_status != 'failed':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Cannot Retry',
                    'message': 'Only failed orders can be retried',
                    'type': 'warning',
                }
            }

        # Reset status to pending and retry
        self.zimra_status = 'pending'
        self.zimra_error = False

        return self.action_fiscalize_manual()

    def _should_fiscalize(self):
        """Return True if the order should be fiscalized"""
        if self.zimra_status in ['fiscalized', 'exempted']:
            return False

        # Don't fiscalize draft orders (quotations) or cancelled orders
        if self.state in ['draft', 'cancel']:
            _logger.info(f"Skipping fiscalization for order {self.name} - State: {self.state}")
            return False

        # For refunds (negative amounts), fiscalize immediately regardless of state
        if self.amount_total < 0:
            return True

        # For positive amounts, only fiscalize paid orders
        if self.amount_total > 0 and self.state in ['draft']:
            _logger.info(f"Skipping fiscalization for order {self.name} - Order in draft (State: {self.state})")
            return False

        return True

    def __create_timestamp(self, dt):
        """
        Converts a datetime to ISO 8601 format with T separator.
        """
        if not dt:
            dt = fields.Datetime.now()
        return dt.replace(microsecond=0).isoformat()

    def _prepare_zimra_invoice_data(self, config):
        """Prepare invoice data for ZIMRA format"""
        # Get tax and currency mappings
        tax_mappings = {tm.odoo_tax_id.id: tm for tm in config.tax_mapping_ids}
        currency_mappings = {cm.odoo_currency_id.id: cm for cm in config.currency_mapping_ids}

        # Get currency code
        currency_code = 'USD'  # Default
        if self.currency_id.id in currency_mappings:
            currency_code = currency_mappings[self.currency_id.id].zimra_currency_code

        # Prepare buyer contact
        buyer_contact = self.__get_buyer_contact()

        # Prepare line items
        line_items = self.__get_line_items(tax_mappings)

        # Check if order has any discounts
        has_discount = any(self.__is_discount_line(line) for line in self.lines)


        # Create timestamp from order date
        timestamp = self.__create_timestamp(self.date_order)
        total_discount = sum(
            float(item.get("DiscountAmount", "0"))
            for item in line_items
        )
        subtotal = self.amount_total - total_discount

        is_refund = self.name.strip().endswith('REFUND')
        # Ensure order has a valid name
        if not self.name or self.name == '/':
            if hasattr(self.config_id, 'sequence_id') and self.config_id.sequence_id:
                self.name = self.config_id.sequence_id.next_by_id()
                _logger.info("Assigned sequence name to POS order %s: %s", self.id, self.name)
            else:
                _logger.warning("POS config %s has no sequence. Cannot assign order name.", self.config_id.name)

        invoice_name= self.name

        data = {
            "InvoiceId": invoice_name,
            "InvoiceNumber": invoice_name,
            "Reference": self.pos_reference or "",
            "IsDiscounted": has_discount,
            "IsTaxInclusive": True,
            "BuyerContact": buyer_contact,
            "Date": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "LineItems": line_items,
            "SubTotal": f"{subtotal - self.amount_tax:.2f}",
            "TotalTax": f"{self.amount_tax:.2f}",
            "Total": f"{self.amount_total:.2f}",
            "CurrencyCode": currency_code,
            "IsRetry": bool(self.zimra_retry_count > 0),
        }

        creditnote = {
            "CreditNoteId": self.name,
            "CreditNoteNumber": self.name,
            "OriginalInvoiceId": re.sub(r'\s+REFUND$', '', self.name).strip(),
            "Reference": self.pos_reference or '',
            "IsTaxInclusive": True,
            "BuyerContact": buyer_contact,
            "Date": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "LineItems": line_items,
            "SubTotal": f"{abs(subtotal - self.amount_tax):.2f}",
            "TotalTax": f"{abs(self.amount_tax):.2f}",
            "Total": f"{abs(self.amount_total + total_discount):.2f}",
            "CurrencyCode": currency_code,
            "IsRetry": bool(self.zimra_retry_count > 0),

        }

        # Final payload: choose credit note if it's a refund, else invoice
        final_payload = creditnote if is_refund else data
        ordername = "Credit Note" if is_refund else "Invoice"

        _logger.info(f"Pos Order {ordername} data: %s", final_payload)
        return final_payload

    def __get_creditnote_line_items(self, tax_mappings):
        """Get line items in ZIMRA credit note format (with absolute values)"""
        line_items = []

        for line in self.lines:
            # Calculate tax information using Odoo's tax computation with absolute values
            tax_amount = 0
            tax_code = ""

            if line.tax_ids:
                # Use Odoo's tax computation with absolute values
                tax_results = line.tax_ids.compute_all(
                    price_unit=abs(line.price_unit),
                    quantity=abs(line.qty),
                    product=line.product_id,
                    partner=self.partner_id if hasattr(self, 'partner_id') else None
                )

                tax_amount = abs(tax_results['total_included'] - tax_results['total_excluded'])

                # Get tax code from mapping
                for tax in line.tax_ids:
                    if tax.id in tax_mappings:
                        tax_mapping = tax_mappings[tax.id]
                        tax_code = tax_mapping.zimra_tax_code
                        break

            # Safely split product name into name and hscode
            try:
                match = re.search(r'\b\d{8,}\b', line.product_id.name)
                if match:
                    hscode = match.group()
                    # Remove the HS code from the name
                    name = re.sub(r'\b' + re.escape(hscode) + r'\b', '', line.product_id.name).strip()
                    # Clean up multiple spaces
                    name = re.sub(r'\s+', ' ', name)
                else:
                    name = line.product_id.name
                    hscode = ''
            except ValueError:
                name = line.product_id.name
                hscode = ''

            # Calculate discount if applicable (ensure positive values)
            discount_amount = 0
            if line.discount:
                discount_amount = abs(line.price_unit * line.qty * line.discount / 100)

            # Ensure all line item values are positive
            unit_amount = abs(line.price_subtotal_incl)
            line_amount = abs(line.price_subtotal_incl)
            quantity = abs(line.qty)

            # Build the line item with absolute values
            line_item = {
                "Description": name,
                "UnitAmount": f"{abs(unit_amount):.3f}",
                "TaxCode": tax_code,
                "ProductCode": hscode,
                "LineAmount": f"{abs(line_amount):.2f}",
                "DiscountAmount": f"{abs(discount_amount):.2f}",
                "Quantity": f"{abs(quantity):.3f}",
            }

            line_items.append(line_item)

        return line_items

    def _get_original_invoice_reference(self):

        # Option 3: Search for related positive order (this is a basic example)
        if self.amount_total < 0:  # This is a refund/credit note
            # Try to find a related positive order - this logic depends on your implementation
            original_order = self.search([
                ('partner_id', '=', self.partner_id.id if self.partner_id else False),
                ('amount_total', '>', 0),
                ('date_order', '<=', self.date_order),
                ('zimra_status', '=', 'fiscalized')
            ], order='date_order desc', limit=1)

            return original_order.name if original_order else ""

        return ""

    def _get_return_reason(self):
        """Get the reason for return/credit note"""

        return "POS Refund"

    def _parse_vat_field(self, vat_string):
        import re

        match_tin = re.search(r'TIN[:=]\s*(\d+)', vat_string or "")
        tin = match_tin.group(1) if match_tin else ''

        match_vat = re.search(r'VAT[:=]\s*(\d+)', vat_string or "")
        vat = match_vat.group(1) if match_vat else ''

        return tin, vat

    def __get_buyer_contact(self):
        """Get buyer contact information"""
        if not self.partner_id:
            return {

            }
        if self.partner_id.company_registry:
            vat = self.partner_id.vat
            tin = self.partner_id.company_registry
        else:
            tin, vat = self._parse_vat_field(self.partner_id.vat)

        return {
            "Name": self.partner_id.name,
            "Tin": tin or None,
            "VatNumber": vat or None,
            "Address": self._get_customer_address() or None,
            "Phone": self.partner_id.phone or None,
            "Email": self.partner_id.email or None,
        }

    def __get_line_items(self, tax_mappings):
        """ZIMRA line items without discount-only lines"""

        # --- Separate product lines and receipt discounts ---
        product_lines = []
        receipt_discount_total = 0.0

        for line in self.lines:
            if self.__is_receipt_discount_line(line):
                receipt_discount_total += abs(line.price_subtotal_incl)
            else:
                product_lines.append(line)

        # Total before receipt discount
        total_before_discount = sum(
            l.price_subtotal_incl for l in product_lines
        ) or 1.0  # avoid division by zero

        line_items = []

        for line in product_lines:
            # --- Tax code ---
            tax_code = ""
            for tax in line.tax_ids:
                if tax.id in tax_mappings:
                    tax_code = tax_mappings[tax.id].zimra_tax_code
                    break

            # --- Name & HS code ---
            name = line.product_id.name or ""
            hscode = ""
            match = re.search(r'\b\d{8,}\b', name)
            if match:
                hscode = match.group()
                name = re.sub(r'\b' + re.escape(hscode) + r'\b', '', name).strip()

            # --- Proportional discount allocation ---
            proportional_discount = (
                    receipt_discount_total
                    * (line.price_subtotal_incl / total_before_discount)
            )

            final_line_amount = line.price_subtotal_incl - proportional_discount

            # --- Product discount (line-level) ---
            line_discount = (
                line.price_unit * line.qty * line.discount / 100
                if line.discount else 0
            )

            line_items.append({
                "Description": name,
                "UnitAmount": f"{abs(line.price_unit):.3f}",
                "TaxCode": tax_code,
                "ProductCode": hscode,
                "LineAmount": f"{abs(final_line_amount):.2f}",
                "DiscountAmount": f"{abs(line_discount + proportional_discount):.2f}",
                "Quantity": f"{abs(line.qty):.3f}",
            })

        return line_items

    def __is_receipt_discount_line(self, line):
        name = (line.product_id.name or "").lower()
        return (
                '%' in name
                or 'discount' in name
                or 'loyalty' in name
                or line.price_subtotal_incl < 0
        )

    def __is_discount_line(self, line):
        """Return True if this line represents a discount/loyalty"""
        # 1. Check if the line has a discount percent applied
        if line.discount and line.discount > 0:
            return True

        # 2. Check if product name indicates a discount/loyalty
        name = line.product_id.name.lower() if line.product_id else ""
        discount_keywords = ['discount', 'loyalty', 'voucher', '% off']
        if any(k in name for k in discount_keywords):
            return True

        # 3. Optional: check for negative price lines (refund/discount)
        if line.price_subtotal_incl < 0:
            return True

        return False

    def _get_customer_address(self):
        """Get customer address as a structured dictionary"""
        if not self.partner_id:
            return {}

        return {
            "Province": self.partner_id.state_id.name if self.partner_id.state_id else '',
            "Street": self.partner_id.street2 or '',
            "HouseNo": self.partner_id.street or '',
            "City": self.partner_id.city or ''
        }

    def _get_payment_details(self):
        """Get payment details"""
        payment_details = []

        for payment in self.payment_ids:
            payment_details.append({
                'method': payment.payment_method_id.name,
                'amount': payment.amount,
                'currency': payment.currency_id.name if payment.currency_id else self.currency_id.name
            })

        return payment_details

    @api.model
    def create(self, vals):
        """Override create to auto-fiscalize"""
        order = super(PosOrder, self).create(vals)

        # Auto-fiscalize if configuration allows and order is paid/invoiced/done
        # Don't fiscalize draft orders (quotations)
       # if order.state in ['paid', 'invoiced', 'done']:
        config = self.env['zimra.config'].search([
                ('company_id', '=', order.company_id.id),
                ('active', '=', True),
                ('auto_fiscalize', '=', True)
            ], limit=1)

        if config:
                result = order._send_to_zimra()

                if not result:
                    _logger.error(f"Auto-fiscalization failed for order {order.name}")

        return order



    @api.model
    @api.model
    def create_from_ui(self, orders, draft=False):
        """Intercept POS orders from UI and defer fiscalization until order has proper name"""
        created_result = super().create_from_ui(orders, draft=draft)

        if created_result and isinstance(created_result, (list, tuple)) and isinstance(created_result[0], int):
            order_records = self.browse(created_result)
        else:
            order_records = created_result

        for order in order_records:
            try:
                # Defer fiscalization until name is valid
                if order.name == '/' or not order.name:
                    _logger.info("Deferring fiscalization for order %s", order.id)
                    # you can call a deferred job or just rely on write override later
                    continue
                # Optionally, trigger fiscalization for already named orders
               # order._send_to_zimra()
            except Exception as e:
                _logger.exception("Error scheduling fiscalization for order %s: %s",
                                  getattr(order, 'name', 'Unknown'), str(e))
        return created_result

    def write(self, vals):
        res = super().write(vals)

        for order in self:
            # HARD GUARDS — stop loops
            if order.zimra_attempted:
                continue

            if not order.name or order.name == '/':
                continue

            if order.state not in ['paid', 'done', 'invoiced']:
                continue

            if order.zimra_status not in ['pending', False]:
                continue

            # Lock BEFORE calling send
            order.zimra_attempted = True

            _logger.info(
                "Triggering fiscalization ONCE for order %s",
                order.name
            )

            order._send_to_zimra()

        return res

    def _deferred_fiscalization(self):
        """Trigger fiscalization only if order has a valid name and config"""
        self.ensure_one()

        if not self.name or self.name == '/':
            _logger.info("Order %s not ready for fiscalization yet. Will retry later.", self.id)
            return False

        if self.state not in ['paid', 'done', 'invoiced']:
            _logger.info("Order %s not in posted state. Skipping fiscalization.", self.id)
            return False

        warehouse = self.session_id.config_id.warehouse_id
        if not warehouse:
            raise UserError("No warehouse configured on this POS.")

        config = self.env['zimra.config'].get_active_config(warehouse.id)
        if not config:
            raise UserError(
                ("No active ZIMRA config found for warehouse %s") % warehouse.name
            )

        if not config:
            _logger.warning("No active ZIMRA config found for company %s", self.company_id.name)
            return False

        # Trigger fiscalization
        return self._send_to_zimra()

    def action_view_zimra_logs(self):
        """View ZIMRA logs for this order"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'ZIMRA Logs',
            'res_model': 'zimra.invoice',
            'view_mode': 'tree,form',
            'domain': [('pos_order_id', '=', self.id)],
            'context': {'default_pos_order_id': self.id}
        }

    def action_download_fiscal_pdf(self):
        """Download the fiscal PDF using zimra_config and refresh the page"""
        self.ensure_one()

        if not self.fiscalized_pdf:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'No PDF Available',
                    'message': 'No fiscal PDF is available for this invoice',
                    'type': 'warning',
                }
            }

        config = self.env['zimra.config'].search([
            ('company_id', '=', self.company_id.id),
            ('active', '=', True)
        ], limit=1)

        if not config:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Configuration Error',
                    'message': 'No active ZIMRA configuration found',
                    'type': 'danger',
                }
            }

        try:
            pdf_data = config.download_pdf(self.fiscalized_pdf)

            if isinstance(pdf_data, str):
                attachment_vals = {
                    'name': f'FiscalInvoice_{self.name}.pdf',
                    'type': 'binary',
                    'datas': pdf_data,
                    'res_model': 'account.move',
                    'res_id': self.id,
                    'mimetype': 'application/pdf',
                }

                if self.fiscal_pdf_attachment_id:
                    self.fiscal_pdf_attachment_id.write(attachment_vals)
                else:
                    attachment = self.env['ir.attachment'].create(attachment_vals)
                    self.fiscal_pdf_attachment_id = attachment.id

                # Return actions: download PDF first, then reload page
                return [
                    {
                        'type': 'ir.actions.act_url',
                        'url': f'/web/content/{self.fiscal_pdf_attachment_id.id}?download=true',
                        'target': 'self',
                    },
                    {
                        'type': 'ir.actions.client',
                        'tag': 'reload',  # refresh the page after download
                    }
                ]

            else:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Download Failed',
                        'message': f'Failed to download PDF. Server returned status code: {pdf_data}',
                        'type': 'danger',
                    }
                }

        except Exception as e:
            _logger.error(f"Error downloading fiscal PDF for invoice {self.name}: {str(e)}")
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Download Error',
                    'message': f'Error downloading PDF: {str(e)}',
                    'type': 'danger',
                }
            }

    @api.model
    def cron_retry_failed_fiscalization(self):
        """Cron job to retry failed fiscalization orders"""
        failed_orders = self.search([
            ('zimra_status', '=', 'failed'),
            ('zimra_retry_count', '<', 3)  # Only retry up to 3 times
        ])

        for order in failed_orders:
            try:
                order._send_to_zimra()
                _logger.info(f"Successfully retried fiscalization for order: {order.name}")
            except Exception as e:
                _logger.error(f"Failed to retry fiscalization for order {order.name}: {str(e)}")
