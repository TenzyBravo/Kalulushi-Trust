from school.models import School


def school(request):
    return {"school": School.objects.select_related("settings").filter(status="active").first()}
