# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import json
import re
import logging
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    # ZIMRA Status Fields
    zimra_status = fields.Selection([
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('fiscalized', 'Fiscalized'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
        ('exempted', 'Exempted')
    ], string='ZIMRA Status', default='pending', tracking=True, copy=False)

    zimra_fiscal_number = fields.Char('ZIMRA Fiscal Number', readonly=True, copy=False)
    zimra_response = fields.Text('ZIMRA Response', readonly=True, copy=False)
    zimra_error = fields.Text('ZIMRA Error', readonly=True, copy=False)
    zimra_sent_date = fields.Datetime('ZIMRA Sent Date', readonly=True, copy=False)
    zimra_fiscalized_date = fields.Datetime('ZIMRA Fiscalized Date', readonly=True, copy=False)
    zimra_retry_count = fields.Integer('Retry Count', default=0, copy=False)

    # Additional ZIMRA fields
    zimra_qr_code = fields.Char('ZIMRA QR Code', readonly=True, copy=False)
    zimra_verification_url = fields.Char('ZIMRA Verification URL', readonly=True, copy=False)
    fiscal_pdf_attachment_id = fields.Many2one('ir.attachment', 'Fiscal PDF', readonly=True, copy=False)
    fiscalized_pdf = fields.Char('Fiscalized Pdf', readonly=True, copy=False)

    # ==================== Decimal-safe money helpers ====================
    # Fuel/other products use deliberate 3-decimal unit pricing. Odoo's price
    # fields are Python floats, so naive `price_unit * quantity` arithmetic
    # (and f"{x:.2f}" formatting on the result) can inherit binary floating
    # point error and round the wrong way on an exact half-cent (e.g. an
    # in-memory value of 13636.2749999999 instead of 13636.275). A validator
    # doing exact-decimal arithmetic on the same inputs will round that
    # half-cent up, causing a mismatch. All fiscal money/qty values must be
    # produced via these helpers instead of raw float math + f"{:.2f}".

    _MONEY_Q = Decimal('0.01')
    _QTY_Q = Decimal('0.001')

    @staticmethod
    def _dec(value):
        """Convert an Odoo float/int to Decimal via its string repr.

        Decimal(str(value)) is used deliberately instead of Decimal(value):
        converting a float directly to Decimal preserves its exact (and
        sometimes ugly) binary representation, e.g. Decimal(1.695) ==
        Decimal('1.69499999999999984456877655247808434069156646728515625').
        Going through str() first gives the decimal value the float was
        actually meant to represent.
        """
        if value is None:
            return Decimal('0')
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    @classmethod
    def _quantize_money(cls, value, rounding=ROUND_HALF_UP):
        """Round a value to 2dp using exact decimal arithmetic (half-up)."""
        return cls._dec(value).quantize(cls._MONEY_Q, rounding=rounding)

    @classmethod
    def _quantize_qty(cls, value, rounding=ROUND_HALF_UP):
        """Round a value to 3dp using exact decimal arithmetic (half-up)."""
        return cls._dec(value).quantize(cls._QTY_Q, rounding=rounding)

    def action_fiscalize_invoice(self):
        """Manual fiscalization action for invoices"""
        self.ensure_one()

        # Validate invoice type
        if not self.is_invoice():
            return self._show_notification(
                'Invalid Document',
                'Only customer invoices can be fiscalized',
                'warning'
            )

        # Check if already fiscalized
        if self.zimra_status in ['fiscalized', 'sent']:
            return self._show_notification(
                'Already Fiscalized',
                f'This invoice has already been fiscalized with number: {self.zimra_fiscal_number}',
                'warning'
            )

        # Check if invoice is posted
        if self.state != 'posted':
            return self._show_notification(
                'Invoice Not Posted',
                'Please post the invoice before fiscalizing',
                'warning'
            )

        # Attempt fiscalization
        try:
            result = self._send_to_zimra()

            if result:
                message = f'Invoice {self.name} has been successfully fiscalized'
                if self.zimra_fiscal_number:
                    message += f' with fiscal number: {self.zimra_fiscal_number}'

                if self.fiscal_pdf_attachment_id:
                    pdf_url = f'/web/content/{self.fiscal_pdf_attachment_id.id}?filename=FiscalInvoice_{self.name}.pdf'
                    message += f'. <a href="{pdf_url}" target="_blank" class="btn btn-primary btn-sm">View PDF</a>'

                return self._show_notification(
                    'Fiscalization Successful',
                    message,
                    'success',
                    sticky=bool(self.fiscal_pdf_attachment_id)
                )
            else:
                error_detail = self.zimra_error or 'Unknown error occurred'
                return self._show_notification(
                    'Fiscalization Failed',
                    f'Failed to fiscalize invoice {self.name}. Error: {error_detail}',
                    'danger',
                    sticky=True
                )

        except Exception as e:
            _logger.exception("Unexpected error during manual fiscalization of %s", self.name)
            return self._show_notification(
                'Fiscalization Error',
                f'An unexpected error occurred: {str(e)}',
                'danger',
                sticky=True
            )

    def action_download_fiscal_pdf(self):
        """Download the fiscal PDF using zimra_config download method"""
        self.ensure_one()

        # Validate PDF availability
        if not self.fiscalized_pdf:
            return self._show_notification(
                'No PDF Available',
                'No fiscal PDF is available for this invoice. Please fiscalize the invoice first.',
                'warning'
            )

        try:
            # Get configuration
            config = self._get_active_zimra_config()
            if not config:
                return self._show_notification(
                    'Configuration Error',
                    'No active ZIMRA configuration found for this company',
                    'danger'
                )

            # Download PDF using config's method
            pdf_data = config.download_pdf(self.fiscalized_pdf)

            if isinstance(pdf_data, str):  # Success - PDF data returned
                # Create or update the PDF attachment
                attachment_vals = {
                    'name': f'FiscalInvoice_{self.name}.pdf',
                    'type': 'binary',
                    'datas': pdf_data,
                    'res_model': 'account.move',
                    'res_id': self.id,
                    'mimetype': 'application/pdf',
                    'description': f'Fiscal PDF for invoice {self.name}',
                }

                if self.fiscal_pdf_attachment_id:
                    self.fiscal_pdf_attachment_id.write(attachment_vals)
                else:
                    attachment = self.env['ir.attachment'].create(attachment_vals)
                    self.fiscal_pdf_attachment_id = attachment.id

                return self._show_notification(
                    'PDF Downloaded',
                    'Fiscal PDF has been downloaded and attached successfully',
                    'success'
                )

            else:  # Error - status code returned
                return self._show_notification(
                    'Download Failed',
                    f'Failed to download PDF. Server returned status code: {pdf_data}',
                    'danger'
                )

        except Exception as e:
            _logger.exception("Error downloading fiscal PDF for invoice %s", self.name)
            return self._show_notification(
                'Download Error',
                f'Error downloading PDF: {str(e)}',
                'danger'
            )

    def _send_to_zimra(self):
        """Send invoice to ZIMRA with improved error handling"""
        self.ensure_one()

        try:
            # Get configuration
            config = self._get_active_zimra_config()
            if not config:
                self._mark_as_failed('No active ZIMRA configuration found for this company')
                return False

            # Check if invoice should be fiscalized
            if not self._should_fiscalize():
                self.zimra_status = 'exempted'
                _logger.info("Invoice %s marked as exempted from fiscalization", self.name)
                return True



            try:
                invoice_data = self._prepare_zimra_invoice_data(config)
            except Exception as e:
                # This will capture the actual error and include it in the failed message
                self._mark_as_failed(f'Failed to prepare invoice data : {e}')
                return False

            if not invoice_data:
                self._mark_as_failed('Failed to prepare invoice data for ZIMRA: returned empty or None')
                return False

            # Create invoice log
            zimra_invoice = self._create_zimra_invoice_log(invoice_data)

            # Update status before sending
            self.write({
                'zimra_sent_date': fields.Datetime.now(),
                'zimra_retry_count': self.zimra_retry_count + 1,
            })

            zimra_invoice.write({
                'status': 'sent',
                'sent_date': self.zimra_sent_date,
            })

            # Prepare fiscal data
            fiscal_invoice = json.dumps(invoice_data, separators=(',', ':'), ensure_ascii=False)

            # Determine endpoint
            endpoint = self._determine_endpoint(invoice_data)

            # Send to ZIMRA
            _logger.info("Sending invoice %s to ZIMRA endpoint: %s", self.name, endpoint)
            response_data = config.send_fiscal_data(fiscal_invoice, endpoint)

            if not response_data:
                self._mark_as_failed('No response received from ZIMRA server', zimra_invoice)
                return False

            # Store response
            self.zimra_response = json.dumps(response_data, indent=2) if response_data else ''
            zimra_invoice.write({'response_data': self.zimra_response})

            # Process response
            return self._process_zimra_response(response_data, zimra_invoice)

        except Exception as e:
            error_msg = f"Exception during fiscalization: {str(e)}"
            _logger.exception("Error fiscalizing invoice %s", self.name)
            self._mark_as_failed(error_msg, zimra_invoice if 'zimra_invoice' in locals() else None)
            return False

    def _process_zimra_response(self, response_data, zimra_invoice):
        """Process ZIMRA response with better error handling"""
        try:
            response = response_data[0] if isinstance(response_data, list) else response_data

            if self._is_fiscalization_successful(response):
                qr_data = response.get("QrData", {})

                # pull from both top level and QrData for safety
                fiscal_day = response.get("FiscalDay") or qr_data.get("FiscalDay", "")
                invoice_number = response.get("InvoiceNumber") or qr_data.get("InvoiceNumber", "")

                if not fiscal_day or not invoice_number:
                    self._mark_as_failed(
                        f'Incomplete response from ZIMRA:{response}: missing FiscalDay or InvoiceNumber',
                        zimra_invoice
                    )
                    return False

                self.write({
                    'zimra_status': 'fiscalized',
                    'zimra_fiscal_number': f"{invoice_number}/{fiscal_day}",
                    'zimra_fiscalized_date': fields.Datetime.now(),
                    'zimra_qr_code': qr_data.get('QrCodeUrl', ''),
                    'zimra_verification_url': qr_data.get('VerificationCode', ''),
                    'fiscalized_pdf': response.get('FiscalInvoicePdf', ''),
                    'zimra_error': False,
                })

                zimra_invoice.write({
                    'status': 'fiscalized',
                    'zimra_fiscal_number': self.zimra_fiscal_number,
                    'fiscalized_date': self.zimra_fiscalized_date,
                })

                _logger.info(
                    "Successfully fiscalized invoice %s - Fiscal Number: %s",
                    self.name, self.zimra_fiscal_number
                )

                # AUTO-DOWNLOAD PDF AFTER SUCCESSFUL FISCALIZATION
                if self.fiscalized_pdf:
                    try:
                        _logger.info("Attempting to auto-download PDF for invoice %s", self.name)
                        config = self._get_active_zimra_config()
                        if config:
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
                                _logger.info("Successfully auto-downloaded and stored PDF for invoice %s", self.name)
                            else:
                                _logger.warning("Failed to auto-download PDF for invoice %s. Status code: %s", self.name, pdf_data)
                    except Exception as pdf_error:
                        _logger.error("Error auto-downloading PDF for invoice %s: %s", self.name, str(pdf_error))

                return True

            else:
                error_msg = response.get('Error', 'Unknown error from ZIMRA')
                fiscal_number = response.get('fiscal_number', response.get('RequestId', ''))
                self._mark_as_failed(error_msg, zimra_invoice, fiscal_number)
                return False

        except Exception as e:
            error_msg = f"Error processing ZIMRA response: {str(e)}"
            _logger.exception("Error processing response for invoice %s", self.name)
            self._mark_as_failed(error_msg, zimra_invoice)
            return False

    def _mark_as_failed(self, error_message, zimra_invoice=None, fiscal_number=None):
        """Mark invoice as failed with error details"""
        self.write({
            'zimra_status': 'failed',
            'zimra_error': error_message,
            'zimra_fiscal_number': fiscal_number or self.zimra_fiscal_number,
        })

        if zimra_invoice:
            zimra_invoice.write({
                'status': 'failed',
                'error_message': error_message,
                'zimra_fiscal_number': fiscal_number,
            })

        _logger.error("Invoice %s fiscalization failed: %s", self.name, error_message)

    def _is_fiscalization_successful(self, response_data):
        """Check if fiscalization response indicates success"""
        if not response_data:
            return False

        if isinstance(response_data, list):
            if not response_data:
                return False
            response = response_data[0]
        else:
            response = response_data

        # Success if Error field is empty/None/False
        return not response.get("Error")

    def _should_fiscalize(self):
        """Check if invoice should be fiscalized with detailed logging"""
        # Only fiscalize customer invoices and credit notes
        if not self.is_invoice(include_receipts=True):
            _logger.debug("Invoice %s: Not an invoice type", self.name)
            return False

        # Skip if already fiscalized or exempted
        if self.zimra_status in ['fiscalized', 'exempted']:
            _logger.debug("Invoice %s: Already %s", self.name, self.zimra_status)
            return False

        # Skip if invoice is not posted
        if self.state != 'posted':
            _logger.debug("Invoice %s: Not posted (state: %s)", self.name, self.state)
            return False

        # Only customer invoices and credit notes
        if self.move_type not in ['out_invoice', 'out_refund']:
            _logger.debug("Invoice %s: Invalid move_type: %s", self.name, self.move_type)
            return False

        _logger.debug("Invoice %s: Should be fiscalized", self.name)
        return True

    def _get_active_zimra_config(self):
        """Get active ZIMRA configuration, preferring warehouse-based lookup"""
        # Try warehouse-based lookup first (consistent with pos.order)
        warehouse = getattr(self, 'warehouse_id', False)
        if warehouse:
            config = self.env['zimra.config'].get_active_config(warehouse.id)
            if config:
                return config

        # Fallback to company-based lookup
        config = self.env['zimra.config'].search([
            ('company_id', '=', self.company_id.id),
            ('active', '=', True)
        ], limit=1)

        if not config:
            _logger.error("No active ZIMRA configuration found for Company %s", self.company_id.name)

        return config

    def _create_zimra_invoice_log(self, invoice_data):
        """Create ZIMRA invoice log entry"""
        return self.env['zimra.invoice'].create({
            'name': self.name,
            'account_move_id': self.id,
            'status': 'pending',
            'request_data': json.dumps(invoice_data, indent=2),
            'company_id': self.company_id.id,
        })

    def _determine_endpoint(self, invoice_data):
        """Determine API endpoint based on invoice type"""
        # Check for credit note
        if "CreditNoteId" in invoice_data and invoice_data["CreditNoteId"]:
            return "/creditnote"

        # Fallback: check invoice ID for 'refund'
        invoice_id = invoice_data.get("InvoiceId", "").strip().lower()
        if "refund" in invoice_id:
            return "/creditnote"

        return "/invoice"

    def _prepare_zimra_invoice_data(self, config):
        """Prepare invoice data for ZIMRA format with validation"""
        try:
            if not config:
                raise ValidationError(f"Invoice {self.name}: No ZIMRA configuration provided")

            # Get tax and currency mappings
            if not config.tax_mapping_ids:
                raise ValidationError(f"Invoice {self.name}: No tax mappings defined in ZIMRA config {config.name}")

            if not config.currency_mapping_ids:
                raise ValidationError(
                    f"Invoice {self.name}: No currency mappings defined in ZIMRA config {config.name}")

            tax_mappings = {tm.odoo_tax_id.id: tm for tm in config.tax_mapping_ids}
            currency_mappings = {cm.odoo_currency_id.id: cm for cm in config.currency_mapping_ids}

            # Get currency code
            currency_code = 'USD'  # Default
            if self.currency_id.id in currency_mappings:
                currency_code = currency_mappings[self.currency_id.id].zimra_currency_code

            # Prepare buyer contact
            buyer_contact = self._get_buyer_contact()

            # Prepare line items
            line_items = self._get_line_items(tax_mappings)

            if not line_items:
                raise ValidationError(f"Invoice {self.name}: No valid line items found. "
                                      f"Check invoice lines and tax mappings.")

            # Create timestamp
            timestamp = self._create_timestamp(self.invoice_date or fields.Date.today())

            # Determine if this is a credit note
            is_credit_note = self.move_type == 'out_refund'
            has_discount = any(line.discount > 0 for line in self.invoice_line_ids)

            # Determine tax-inclusivity from the taxes on invoice lines
            # Check if any tax has price_include=True (tax included in price)
            is_tax_inclusive = any(
                tax.price_include
                for line in self.invoice_line_ids
                for tax in line.tax_ids
            )

            # Header totals: derive from the line items we just built (which are
            # already exact-decimal) rather than re-deriving from Odoo's float
            # amount_* fields, so header and line data can never disagree.
            line_amount_sum = sum(
                (Decimal(li["LineAmount"]) for li in line_items), Decimal('0.00')
            )
            # Subtotal/tax split still comes from Odoo's own totals (tax computation
            # itself isn't the reported bug), just quantized safely via Decimal.
            subtotal_dec = self._quantize_money(abs(self.amount_untaxed))
            tax_dec = self._quantize_money(abs(self.amount_tax))
            total_dec = self._quantize_money(abs(self.amount_total))

            if is_credit_note:
                data = {
                    "CreditNoteId": self.name,
                    "CreditNoteNumber": self.name,
                    "OriginalInvoiceId": self.reversed_entry_id.name if self.reversed_entry_id else "",
                    "Reference": self.ref or '',
                    "IsTaxInclusive": is_tax_inclusive,
                    "IsDiscounted": has_discount,
                    "BuyerContact": buyer_contact,
                    "Date": timestamp,
                    "LineItems": line_items,
                    "SubTotal": f"{subtotal_dec}",
                    "TotalTax": f"{tax_dec}",
                    "Total": f"{total_dec}",
                    "CurrencyCode": currency_code,
                    "IsRetry": bool(self.zimra_retry_count > 0),
                }
            else:
                data = {
                    "InvoiceId": self.name,
                    "InvoiceNumber": self.name,
                    "Reference": self.ref or "",
                    "IsDiscounted": has_discount,
                    "IsTaxInclusive": is_tax_inclusive,
                    "BuyerContact": buyer_contact,
                    "Date": timestamp,
                    "LineItems": line_items,
                    "SubTotal": f"{subtotal_dec}",
                    "TotalTax": f"{tax_dec}",
                    "Total": f"{total_dec}",
                    "CurrencyCode": currency_code,
                    "IsRetry": bool(self.zimra_retry_count > 0),
                }

            _logger.info("Prepared %s data for %s", 'Credit Note' if is_credit_note else 'Invoice', self.name)
            return data

        except Exception as e:
            # Raise the error instead of returning None
            _logger.exception("Error preparing  data for invoice %s", self.name)
            raise ValidationError(f"Failed to prepare  data for invoice {self.name}: {e}")

    def _get_buyer_contact(self):
        """Get buyer contact information"""
        if not self.partner_id:
            return {}

        # Handle TIN/VAT parsing
        if self.partner_id.company_registry:
            vat = self.partner_id.vat or None
            tin = self.partner_id.company_registry or None
        else:
            tin, vat = self._parse_vat_field(self.partner_id.vat)

        return {
            "Name": self.partner_id.name or "",
            "Tin": tin or None,
            "VatNumber": vat or None,
            "Address": self._get_customer_address(),
            "Phone": self.partner_id.phone or "",
            "Email": self.partner_id.email or ""
        }

    def _parse_vat_field(self, vat_string):
        """Parse VAT string to extract TIN and VAT numbers"""
        if not vat_string:
            return None, None

        match_tin = re.search(r'TIN[:=]\s*(\d+)', vat_string)
        tin = match_tin.group(1) if match_tin else ''

        match_vat = re.search(r'VAT[:=]\s*(\d+)', vat_string)
        vat = match_vat.group(1) if match_vat else ''

        return tin, vat

    def _get_line_items(self, tax_mappings):
        """Get line items in ZIMRA format with validation"""
        line_items = []

        for line in self.invoice_line_ids:
            # Skip non-product lines
            if line.display_type in ['line_section', 'line_note']:
                continue

            try:
                line_item = self._prepare_line_item(line, tax_mappings)
                if line_item:
                    line_items.append(line_item)
            except Exception as e:
                _logger.error("Error preparing line item for %s: %s", line.name, str(e))
                continue

        return line_items

    def _prepare_line_item(self, line, tax_mappings):
        """Prepare a single line item with error handling.

        """
        # Skip section and note lines
        if line.display_type in ('line_section', 'line_note'):
            return None

        # Calculate tax information
        tax_code = ""
        if line.tax_ids:
            for tax in line.tax_ids:
                _logger.info("Preparing tax code %s", line.tax_ids.name)
                if tax.id in tax_mappings:
                    tax_code = tax_mappings[tax.id].zimra_tax_code
                    break

        # Parse product name and HS code
        name, hscode = self._parse_product_name(line)

        # Calculate amounts based on invoice type
        is_refund = self.move_type == 'out_refund'

        # Convert Odoo float fields to Decimal via str() first (see _dec docstring)
        # before any arithmetic
        quantity_dec = self._dec(abs(line.quantity) if is_refund else line.quantity)
        unit_amount_dec = self._dec(abs(line.price_unit) if is_refund else line.price_unit)
        subtotal_raw_dec = self._dec(abs(line.price_subtotal) if is_refund else line.price_subtotal)
        price_total_raw_dec = self._dec(abs(line.price_total) if is_refund else line.price_total)


        gross_amount_dec = (unit_amount_dec * quantity_dec).quantize(
            self._MONEY_Q, rounding=ROUND_HALF_UP
        )
        subtotal_dec = subtotal_raw_dec.quantize(self._MONEY_Q, rounding=ROUND_HALF_UP)
        discount_amount_dec = gross_amount_dec - subtotal_dec


        price_total_dec = price_total_raw_dec.quantize(self._MONEY_Q, rounding=ROUND_HALF_UP)
        tax_portion_dec = price_total_dec - subtotal_dec
        line_total_dec = gross_amount_dec + tax_portion_dec

        # Build line item
        return {
            "Description": name or line.name or "",
            "UnitAmount": f"{self._quantize_qty(unit_amount_dec)}",
            "TaxCode": tax_code,
            "ProductCode": hscode or "",
            "LineAmount": f"{line_total_dec}",
            "DiscountAmount": f"{discount_amount_dec}",
            "Quantity": f"{self._quantize_qty(quantity_dec)}",
        }

    def _parse_product_name(self, line):
        """Safely extract product name and HS code from line"""
        if not line.product_id:
            # Fallback for service or manual line
            return line.name or "Service", ''

        # Safely check if product has HS code field
        hscode = getattr(line.product_id, 'l10n_hs_code', '') or ''
        name = line.product_id.name or "Unnamed Product"

        if not hscode:
            try:
                # Look for an 8+ digit HS code in the product name
                match = re.search(r'\b\d{8,}\b', name)
                if match:
                    hscode = match.group()
                    # Remove the HS code from the name
                    name = re.sub(r'\b' + re.escape(hscode) + r'\b', '', name).strip()
                    # Clean up multiple spaces
                    name = re.sub(r'\s+', ' ', name)
            except ValueError:
                _logger.warning("Regex failed when parsing product name for line '%s'", line.name)
                pass

        return name, hscode

    def _create_timestamp(self, date_field):
        """Create timestamp in ISO format"""
        if not date_field:
            date_field = fields.Datetime.now()

        if isinstance(date_field, str):
            return date_field

        # Convert date to datetime if needed
        if hasattr(date_field, 'hour'):  # Already datetime
            return date_field.replace(microsecond=0).isoformat()
        else:  # It's a date
            dt = datetime.combine(date_field, datetime.min.time())
            return dt.replace(microsecond=0).isoformat()

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

    def _show_notification(self, title, message, notification_type, sticky=False):
        """Helper to show notifications"""
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': notification_type,
                'sticky': sticky,
            }
        }

    # ==================== Override Methods ====================

    def action_post(self):
        """Override action_post - auto-fiscalization disabled"""
        result = super(AccountMove, self).action_post()

        # Auto-fiscalization disabled - use manual fiscalization only
        # Users must click "Fiscalize Invoice" button to send to ZIMRA

        return result

    def button_cancel(self):
        """Override cancel to handle ZIMRA cancellation"""
        result = super(AccountMove, self).button_cancel()

        for move in self:
            if move.zimra_status == 'fiscalized':
                move.write({
                    'zimra_status': 'cancelled',
                })
                _logger.info("Cancelled fiscalized invoice %s", move.name)

        return result

    def button_draft(self):
        """Override draft to reset ZIMRA status"""
        result = super(AccountMove, self).button_draft()

        for move in self:
            if move.zimra_status in ['fiscalized', 'sent', 'failed']:
                move.write({
                    'zimra_status': 'pending',
                    'zimra_fiscal_number': False,
                    'zimra_response': False,
                    'zimra_error': False,
                    'zimra_sent_date': False,
                    'zimra_fiscalized_date': False,
                    'zimra_qr_code': False,
                    'zimra_verification_url': False,
                    'fiscal_pdf_attachment_id': False,
                    'fiscalized_pdf': False,
                    'zimra_retry_count': 0,
                })
                _logger.info("Reset ZIMRA status for invoice %s", move.name)

        return result

    @api.model
    def create(self, vals):
        """Override create to set initial ZIMRA status"""
        move = super(AccountMove, self).create(vals)

        if move.is_invoice() and move.move_type in ['out_invoice', 'out_refund']:
            move.zimra_status = 'pending'
        else:
            move.zimra_status = 'exempted'

        return move

    def write(self, vals):
        return super().write(vals)

    # ==================== Action Methods ====================

    def action_retry_fiscalization(self):
        """Retry fiscalization for failed invoices"""
        self.ensure_one()

        if self.zimra_status not in ['failed', 'pending']:
            return self._show_notification(
                'Cannot Retry',
                f'Only failed or pending invoices can be retried. Current status: {self.zimra_status}',
                'warning'
            )

        # Check retry limit
        if self.zimra_retry_count >= 5:
            return self._show_notification(
                'Retry Limit Reached',
                f'Maximum retry attempts (5) reached for this invoice. Please check the error and contact support.',
                'warning',
                sticky=True
            )

        # Reset error and retry
        self.write({
            'zimra_status': 'pending',
            'zimra_error': False,
        })

        return self.action_fiscalize_invoice()

    def action_view_zimra_logs(self):
        """View ZIMRA logs for this invoice"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'ZIMRA Logs - {self.name}',
            'res_model': 'zimra.invoice',
            'view_mode': 'list,form',
            'domain': [('account_move_id', '=', self.id)],
            'context': {'default_account_move_id': self.id}
        }

    @api.model
    def cron_retry_failed_fiscalization(self):
        """Cron job to retry failed fiscalization for invoices"""
        # Only retry invoices with less than 3 attempts
        failed_invoices = self.search([
            ('zimra_status', '=', 'failed'),
            ('zimra_retry_count', '<', 3),
            ('state', '=', 'posted')
        ])

        _logger.info("Cron: Found %d failed invoices to retry", len(failed_invoices))

        success_count = 0
        fail_count = 0

        for invoice in failed_invoices:
            try:
                result = invoice._send_to_zimra()
                if result:
                    success_count += 1
                    _logger.info("Cron: Successfully retried fiscalization for invoice: %s", invoice.name)
                else:
                    fail_count += 1
                    _logger.warning("Cron: Retry failed for invoice %s: %s", invoice.name, invoice.zimra_error)
            except Exception as e:
                fail_count += 1
                _logger.exception("Cron: Exception during retry for invoice %s", invoice.name)

        _logger.info("Cron completed: %d successful, %d failed", success_count, fail_count)