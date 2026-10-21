from django.urls import path

from . import views

urlpatterns = [
    path("", views.student_list, name="student_list"),
    path("add/", views.student_add, name="student_add"),
]
