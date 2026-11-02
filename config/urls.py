from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from accounts import views as staff
from accounts.forms import SignInForm
from learners import views as learners

urlpatterns = [
    path("", staff.home, name="home"),
    path("login/", auth_views.LoginView.as_view(authentication_form=SignInForm), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password/", auth_views.PasswordChangeView.as_view(success_url="/"), name="password_change"),

    path("learners/", learners.learner_list, name="learner_list"),
    path("learners/register/", learners.register, name="learner_register"),
    path("learners/import/", learners.import_view, name="learner_import"),
    path("learners/<int:pk>/", learners.learner_detail, name="learner_detail"),
    path("learners/<int:pk>/edit/", learners.edit, name="learner_edit"),
    path("learners/<int:pk>/status/", learners.change_status, name="learner_status"),
    path("learners/<int:pk>/guardians/add/", learners.add_guardian, name="guardian_add"),
    path("learners/<int:pk>/guardians/<int:link_pk>/edit/", learners.edit_link, name="link_edit"),
    path("learners/<int:pk>/guardians/<int:link_pk>/role/", learners.set_contact_role, name="link_role"),
    path("learners/<int:pk>/guardians/<int:link_pk>/remove/", learners.remove_link, name="link_remove"),
    path("learners/<int:pk>/guardians/<int:link_pk>/details/", learners.edit_guardian, name="guardian_edit"),
    path("learners/<int:pk>/guardians/<int:link_pk>/phone/", learners.change_phone, name="guardian_phone"),
    path("learners/<int:pk>/guardians/<int:link_pk>/verify/", learners.verify_phone, name="guardian_verify"),
    path("learners/<int:pk>/enrolments/add/", learners.enroll, name="enrollment_add"),
    path("learners/<int:pk>/enrolments/<int:enrollment_pk>/transfer/", learners.transfer, name="enrollment_transfer"),
    path("learners/<int:pk>/enrolments/<int:enrollment_pk>/correct/", learners.correct, name="enrollment_correct"),
    path("learners/<int:pk>/notes/add/", learners.add_note, name="note_add"),
    path("learners/<int:pk>/notes/<int:note_pk>/archive/", learners.archive_note, name="note_archive"),

    path("staff/", staff.staff_list, name="staff_list"),
    path("staff/new/", staff.staff_create, name="staff_create"),
    path("staff/<int:pk>/", staff.staff_detail, name="staff_detail"),
    path("staff/<int:pk>/edit/", staff.staff_edit, name="staff_edit"),
    path("staff/<int:pk>/roles/", staff.staff_roles, name="staff_roles"),
    path("staff/<int:pk>/deactivate/", staff.staff_deactivate, name="staff_deactivate"),
    path("staff/<int:pk>/sessions/<int:session_pk>/revoke/", staff.revoke_session, name="session_revoke"),

    path("admin/", admin.site.urls),
]
