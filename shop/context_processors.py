from .models import StoreSetting


def store_settings(request):
    setting, created = StoreSetting.objects.get_or_create(id=1)
    return {
        "store_setting": setting,
        "store_name": setting.store_name or "Prime Shop",
        "store_currency": setting.currency or "AFN",
    }
