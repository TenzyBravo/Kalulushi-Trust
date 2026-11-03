from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="reports"),
    path("exports/", views.exports_log, name="report_exports"),
    path("<slug:name>/", views.report, name="report"),
]
