from django.contrib import admin
from .models import (
    Product,
    ProductVariant,
    ProductDetail,
    Supplier,
    Purchase,
    PurchaseItem,
    Customer,
    Sale,
    SaleItem,
    Invoice,
    SaleItemAllocation,
    BatchExpense,
    Expense,
    StoreSetting,
    VariantDetail,
    StockLocation,
    CustomerPayment,
    SupplierPayment,
    StockTransfer,
    SaleReturn,
    ActivityLog,
    Investor,
    InvestmentRound,
    RoundInvestment,
    InvestorWithdrawal,
    RoundBatch,
)

class ProductDetailInline(admin.TabularInline):
    model = ProductDetail
    extra = 1


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1


class ProductAdmin(admin.ModelAdmin):
    inlines = [ProductDetailInline, ProductVariantInline]
    list_display = ("name", "category")


class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ("product", "size", "color", "model", "selling_price", "current_stock")
    list_filter = ("product", "size", "color")
    search_fields = ("product__name", "size", "color", "model", "sku")


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 1


class PurchaseAdmin(admin.ModelAdmin):
    inlines = [PurchaseItemInline]


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 1


class SaleAdmin(admin.ModelAdmin):
    inlines = [SaleItemInline]


admin.site.register(Product, ProductAdmin)
admin.site.register(ProductVariant, ProductVariantAdmin)
admin.site.register(Supplier)
admin.site.register(Purchase, PurchaseAdmin)
admin.site.register(Customer)
admin.site.register(Sale, SaleAdmin)
admin.site.register(Invoice)
admin.site.register(PurchaseItem)
admin.site.register(SaleItem)
admin.site.register(SaleItemAllocation)
admin.site.register(BatchExpense)
admin.site.register(Expense)
admin.site.register(StoreSetting)
admin.site.register(VariantDetail)
admin.site.register(StockLocation)
admin.site.register(CustomerPayment)
admin.site.register(SupplierPayment)
admin.site.register(StockTransfer)
admin.site.register(SaleReturn)
admin.site.register(ActivityLog)
admin.site.register(Investor)
admin.site.register(InvestmentRound)
admin.site.register(RoundInvestment)
admin.site.register(InvestorWithdrawal)
admin.site.register(RoundBatch)
