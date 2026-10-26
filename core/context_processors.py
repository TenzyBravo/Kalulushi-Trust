from .models import School


def school(request):
    return {"school": School.current()}
