# zimra_fiscal/__manifest__.py
# -*- coding: utf-8 -*-
{
    'name': 'Fiscal Harmony Integration',
    'version': '1.0.0',
    'category': 'Accounting/Localizations',
    'summary': 'Real-time ZIMRA fiscal integration for POS invoices',
    'description': """
        This module provides real-time integration with ZIMRA fiscal services
        for Point of Sale invoices. Features include:
        - Automatic fiscalization of POS invoices
        - Configuration management for API keys and mappings
        - Manual fiscalization for failed transactions
        - Currency and tax mapping configuration
    """,
    'author': 'FISCAL HARMONY',
    'website': 'https://fiscalharmony.co.zw/',
    'depends': ['base', 'point_of_sale', 'account'],
    'data': [
        'security/ir.model.access.csv',
        'views/zimra_config_views.xml',
        'views/menu_views.xml',
        #'views/pos_order_views.xml',
        'views/invoices_view.xml',
       # 'views/menu_views.xml',
        #'data/zimra_data.xml',
    ],
   # 'demo': [
      #  'demo/demo_data.xml',
   # ],
    'installable': True,
    'application': True,
    'auto_install': True,
    'license': 'LGPL-3',
}

