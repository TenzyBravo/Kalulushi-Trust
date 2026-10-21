from .models import SchoolSettings


def school_settings(request):
    return {"school": SchoolSettings.objects.filter(pk=1).first()}
