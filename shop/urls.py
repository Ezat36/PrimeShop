from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),

    path('products/add/', views.product_add, name='product_add'),
    path('products/<int:product_id>/manage/', views.product_manage, name='product_manage'),
    path('products/<int:product_id>/details/add/', views.product_detail_add, name='product_detail_add'),
    path('products/<int:product_id>/variants/add/', views.product_variant_add, name='product_variant_add'),
    path('products/', views.product_list, name='product_list'),

    path('stock-report/', views.stock_report, name='stock_report'),
    path('profit-loss-report/', views.profit_loss_report, name='profit_loss_report'),
    path('purchases/', views.purchase_list, name='purchase_list'),
    path('purchases/add/', views.purchase_add, name='purchase_add'),
    path('sales/', views.sale_list, name='sale_list'),
    path('sales/add/', views.sale_add, name='sale_add'),
    path('customers/', views.customer_list, name='customer_list'),
    path('customers/add/', views.customer_add, name='customer_add'),
    path('invoices/', views.invoice_list, name='invoice_list'),
    path('invoices/<int:invoice_id>/', views.invoice_detail, name='invoice_detail'),
    path('suppliers/', views.supplier_list, name='supplier_list'),
    path('suppliers/add/', views.supplier_add, name='supplier_add'),

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

    path('expenses/', views.expense_list, name='expense_list'),
    path('expenses/add/', views.expense_add, name='expense_add'),

    path('batch-stock-report/', views.batch_stock_report, name='batch_stock_report'),

    path('batch-profit-report/', views.batch_profit_report, name='batch_profit_report'),
    path('batch-expenses/', views.batch_expense_list, name='batch_expense_list'),
    path('batch-expenses/add/', views.batch_expense_add, name='batch_expense_add'),

path('users/<int:user_id>/edit/', views.user_edit, name='user_edit'),]

