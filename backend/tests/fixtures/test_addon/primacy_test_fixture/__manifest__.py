{
    'name': 'Primacy Test Fixture',
    'version': '19.0.1.0.0',
    'summary': 'Minimal fixture addon for indexer and golden-module CI tests.',
    'depends': ['base', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'views/leave_request_views.xml',
    ],
    'installable': True,
    'license': 'LGPL-3',
}
