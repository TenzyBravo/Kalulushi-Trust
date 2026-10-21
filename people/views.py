from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.db.models import Q
from django.shortcuts import redirect, render

from .forms import StudentForm
from .models import Student


@permission_required("people.view_student", raise_exception=True)
def student_list(request):
    query = request.GET.get("q", "").strip()
    students = Student.objects.filter(is_active=True).prefetch_related("guardian_links__guardian")
    if query:
        students = students.filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(admission_number__icontains=query)
        )
    return render(request, "people/student_list.html", {"students": students[:200], "query": query})


@permission_required("people.add_student", raise_exception=True)
def student_add(request):
    form = StudentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        student = form.save()
        messages.success(request, f"Added {student.full_name}.")
        return redirect("student_list")
    return render(request, "people/student_form.html", {"form": form})
