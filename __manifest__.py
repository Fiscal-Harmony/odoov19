# -*- coding: utf-8 -*-
{
    'name': 'Fiscal Harmony Integration',
    'version': '1.1.0',
    'category': 'Accounting/Localizations',
    'summary': 'Real-time ZIMRA fiscal integration for POS and accounting invoices',
    'description': """
        This module provides real-time integration with ZIMRA fiscal services
        for Point of Sale and accounting invoices. Features include:
        - Automatic fiscalization of POS invoices
        - Automatic fiscalization of accounting invoices
        - Configuration management for API keys and mappings
        - Manual fiscalization for failed transactions
        - Currency and tax mapping configuration
        - Fiscal PDF attachment and QR code display
        - Dashboard with charts and error tracking
    """,
    'author': 'FISCAL HARMONY',
    'website': 'https://fiscalharmony.co.zw/',
    'depends': ['base', 'point_of_sale', 'account'],
    'data': [
        'security/ir.model.access.csv',
        'views/zimra_config_views.xml',
        'views/menu_views.xml',
        'views/invoices_view.xml',
        'data/ir_cron_data.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'odoov19/static/src/js/pos_store_patch.js',
            'odoov19/static/src/xml/receipt_inherit.xml',
        ],
        'web.assets_backend': [
            'odoov19/static/src/js/show_fiscal_pdf.js',
            'odoov19/static/src/js/fiscal_pdf_bus_listener.js',
            'odoov19/static/src/js/dashboard.js',
            'odoov19/static/src/xml/show_fiscal_pdf.xml',
            'odoov19/static/src/xml/dashboard.xml',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
