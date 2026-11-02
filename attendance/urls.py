from django.urls import path

from . import views

urlpatterns = [
    path("", views.today, name="attendance_today"),
    path("class/<int:pk>/", views.register, name="attendance_register"),
    path("follow-up/", views.followup, name="attendance_followup"),
    path("follow-up/<int:pk>/", views.case_detail, name="attendance_case"),
    path("calendar/", views.calendar, name="attendance_calendar"),
    path("calendar/<int:pk>/end/", views.calendar_end, name="attendance_calendar_end"),
]
