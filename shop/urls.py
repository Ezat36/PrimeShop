from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),

    path('products/add/', views.product_add, name='product_add'),
    path('products/<int:product_id>/manage/', views.product_manage, name='product_manage'),
    path('products/<int:product_id>/details/add/', views.product_detail_add, name='product_detail_add'),
    path('products/<int:product_id>/variants/add/', views.product_variant_add, name='product_variant_add'),
    path('variants/<int:variant_id>/edit/', views.product_variant_edit, name='product_variant_edit'),
    path('variants/<int:variant_id>/delete/', views.product_variant_delete, name='product_variant_delete'),
    path('products/', views.product_list, name='product_list'),

    path('stock-report/', views.stock_report, name='stock_report'),
    path('stock-report/<int:variant_id>/movements/', views.stock_movement_report, name='stock_movement_report'),
    path('profit-loss-report/', views.profit_loss_report, name='profit_loss_report'),
    path('user-sales-report/', views.user_sales_report, name='user_sales_report'),
    path('purchases/', views.purchase_list, name='purchase_list'),
    path('purchases/add/', views.purchase_add, name='purchase_add'),
    path('sales/', views.sale_list, name='sale_list'),
    path('sales/pos/', views.pos_sale, name='pos_sale'),
    path('sales/add/', views.sale_add, name='sale_add'),
    path('sales/<int:sale_id>/cancel/', views.sale_cancel, name='sale_cancel'),
    path('sales/returns/', views.sale_return_list, name='sale_return_list'),
    path('sales/returns/add/', views.sale_return_add, name='sale_return_add'),
    path('customers/', views.customer_list, name='customer_list'),
    path('customers/add/', views.customer_add, name='customer_add'),
    path('customers/<int:customer_id>/', views.customer_detail, name='customer_detail'),
    path('customers/payments/', views.customer_payment_list, name='customer_payment_list'),
    path('customers/payments/add/', views.customer_payment_add, name='customer_payment_add'),
    path('invoices/', views.invoice_list, name='invoice_list'),
    path('invoices/<int:invoice_id>/', views.invoice_detail, name='invoice_detail'),
    path('suppliers/', views.supplier_list, name='supplier_list'),
    path('suppliers/add/', views.supplier_add, name='supplier_add'),
    path('suppliers/<int:supplier_id>/edit/', views.supplier_edit, name='supplier_edit'),
    path('suppliers/<int:supplier_id>/delete/', views.supplier_delete, name='supplier_delete'),
    path('suppliers/<int:supplier_id>/', views.supplier_detail, name='supplier_detail'),
    path('suppliers/payments/', views.supplier_payment_list, name='supplier_payment_list'),
    path('suppliers/payments/add/', views.supplier_payment_add, name='supplier_payment_add'),

    path('users/', views.user_list, name='user_list'),
    path('users/add/', views.user_add, name='user_add'),
    path('users/<int:user_id>/toggle/', views.user_toggle_active, name='user_toggle_active'),

    path('groups/<int:group_id>/permissions/', views.group_permissions, name='group_permissions'),
    path('groups/add/', views.group_add, name='group_add'),
    path('groups/', views.group_list, name='group_list'),

    path('login/', views.user_login, name='login'),
    path('logout/', views.user_logout, name='logout'),
    path('profile/', views.user_profile, name='profile'),

    path('profile/edit/', views.profile_edit, name='profile_edit'),
    path('profile/change-password/', views.change_password, name='change_password'),

    path('settings/', views.settings_page, name='settings_page'),
    path('backup-restore/', views.backup_restore_page, name='backup_restore_page'),
    path('backup-restore/download/', views.backup_download, name='backup_download'),
    path('backup-restore/restore/', views.backup_restore, name='backup_restore'),

    path('expenses/', views.expense_list, name='expense_list'),
    path('expenses/add/', views.expense_add, name='expense_add'),

    path('batch-stock-report/', views.batch_stock_report, name='batch_stock_report'),

    path('batch-profit-report/', views.batch_profit_report, name='batch_profit_report'),
    path('batch-expenses/', views.batch_expense_list, name='batch_expense_list'),
    path('batch-expenses/add/', views.batch_expense_add, name='batch_expense_add'),
    path('batches/<str:batch_number>/', views.batch_detail, name='batch_detail'),
    path('users/<int:user_id>/edit/', views.user_edit, name='user_edit'),
    
    path('stock-locations/', views.stock_location_list, name='stock_location_list'),
    path('stock-locations/add/', views.stock_location_add, name='stock_location_add'),
    path('stock-locations/<int:location_id>/edit/', views.stock_location_edit, name='stock_location_edit'),
    path('stock-locations/<int:location_id>/delete/', views.stock_location_delete, name='stock_location_delete'),
    path('stock-location-report/', views.stock_location_report, name='stock_location_report'),
    path('low-stock-alerts/', views.low_stock_alerts, name='low_stock_alerts'),
    path('stock-transfers/', views.stock_transfer_list, name='stock_transfer_list'),
    path('stock-transfers/add/', views.stock_transfer_add, name='stock_transfer_add'),
    path('stock-adjustments/', views.stock_adjustment_list, name='stock_adjustment_list'),
    path('stock-adjustments/add/', views.stock_adjustment_add, name='stock_adjustment_add'),
    path('activity-logs/', views.activity_log_list, name='activity_log_list'),
    path('exports/<str:report_type>/', views.export_data, name='export_data'),
    
]

