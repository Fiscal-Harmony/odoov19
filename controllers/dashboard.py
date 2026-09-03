# -*- coding: utf-8 -*-
import logging
from datetime import timedelta, date

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class FiscalDashboard(http.Controller):

    @http.route('/fiscalharmony/dashboard/data', type='jsonrpc', auth='user')
    def get_dashboard_data(self, **kwargs):
        try:
            days = int(kwargs.get('days', 1))
            invoice_model = request.env['zimra.invoice'].sudo()

            today = date.today()
            if days > 0:
                start_date = today - timedelta(days=days)
            else:
                start_date = today - timedelta(days=365)

            domain = [('create_date', '>=', str(start_date))]
            all_records = invoice_model.search(domain)
            total = len(all_records)

            fiscalized = failed = pending = sent = cancelled = 0
            for rec in all_records:
                if rec.status == 'fiscalized':
                    fiscalized += 1
                elif rec.status == 'failed':
                    failed += 1
                elif rec.status == 'pending':
                    pending += 1
                elif rec.status == 'sent':
                    sent += 1
                elif rec.status == 'cancelled':
                    cancelled += 1

            # Daily data for chart
            daily_data = []
            current = start_date
            while current <= today:
                next_day = current + timedelta(days=1)
                current_str = str(current)
                next_str = str(next_day)
                day_fiscalized = 0
                day_failed = 0
                day_total = 0
                for r in all_records:
                    cd = r.create_date
                    if not cd:
                        continue
                    if hasattr(cd, 'date'):
                        rd = cd.date()
                    else:
                        rd = date.fromisoformat(str(cd)[:10])
                    if rd == current:
                        day_total += 1
                        if r.status == 'fiscalized':
                            day_fiscalized += 1
                        elif r.status == 'failed':
                            day_failed += 1
                if day_total > 0:
                    daily_data.append({
                        'date': current_str,
                        'total': day_total,
                        'fiscalized': day_fiscalized,
                        'failed': day_failed,
                    })
                current = next_day

            # Common errors
            error_counts = {}
            for record in all_records:
                if record.status == 'failed' and record.error_message:
                    error_msg = (record.error_message or 'Unknown error').strip()[:100]
                    error_counts[error_msg] = error_counts.get(error_msg, 0) + 1

            common_errors = sorted(
                [{'error': k, 'count': v} for k, v in error_counts.items()],
                key=lambda x: x['count'],
                reverse=True
            )[:10]

            return {
                'summary': {
                    'total': total,
                    'fiscalized': fiscalized,
                    'failed': failed,
                    'pending': pending,
                    'sent': sent,
                    'cancelled': cancelled,
                },
                'daily_data': daily_data,
                'common_errors': common_errors,
            }

        except Exception as e:
            _logger.exception("Error fetching dashboard data")
            return {
                'summary': {'total': 0, 'fiscalized': 0, 'failed': 0, 'pending': 0, 'sent': 0, 'cancelled': 0},
                'daily_data': [],
                'common_errors': [],
                'error': str(e),
            }
