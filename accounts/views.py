from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from people.models import Student


@login_required
def home(request):
    user = request.user
    context = {
        "can_view_pupils": user.has_perm("people.view_student"),
        "can_add_pupils": user.has_perm("people.add_student"),
    }
    if context["can_view_pupils"]:
        context["pupil_count"] = Student.objects.filter(is_active=True).count()
    return render(request, "home.html", context)
