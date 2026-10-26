from django.urls import path

from . import views

urlpatterns = [
    path("", views.learner_list, name="learner_list"),
    path("register/", views.register, name="learner_register"),
    path("import/", views.import_upload, name="learner_import"),
    path("import/template.xlsx", views.import_template, name="learner_import_template"),
    path("import/preview/", views.import_preview, name="learner_import_preview"),
    path("import/problems.xlsx", views.import_errors, name="learner_import_errors"),
    path("<int:pk>/", views.profile, name="learner_profile"),
    path("<int:pk>/edit/", views.edit, name="learner_edit"),
    path("<int:pk>/status/", views.change_status, name="learner_status"),
    path("<int:pk>/notes/add/", views.note_add, name="learner_note_add"),
    path("<int:pk>/notes/<int:note_pk>/archive/", views.note_archive, name="learner_note_archive"),
    path("<int:pk>/guardians/add/", views.guardian_add, name="learner_guardian_add"),
    path("<int:pk>/guardians/link/<int:link_pk>/", views.link_action, name="learner_link_action"),
    path("<int:pk>/guardians/link/<int:link_pk>/remove/", views.link_remove, name="learner_link_remove"),
    path("<int:pk>/guardians/<int:guardian_pk>/edit/", views.guardian_edit, name="learner_guardian_edit"),
    path("<int:pk>/guardians/<int:guardian_pk>/phone/", views.phone_change, name="learner_phone_change"),
    path("<int:pk>/contacts/<int:contact_pk>/verify/", views.contact_verify, name="learner_contact_verify"),
    path("<int:pk>/transfer/", views.transfer, name="learner_transfer"),
    path("<int:pk>/enroll/", views.enroll, name="learner_enroll"),
    path("<int:pk>/enrollments/<int:enrollment_pk>/reverse/", views.enrollment_reverse, name="learner_enrollment_reverse"),
]
