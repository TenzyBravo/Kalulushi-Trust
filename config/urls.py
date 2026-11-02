from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from accounts import views as accounts_views
from core import views as core_views
from staffing import views as staffing_views

urlpatterns = [
    path("", accounts_views.home, name="home"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password/", auth_views.PasswordChangeView.as_view(success_url="/"), name="password_change"),
    path("learners/", include("learners.urls")),
    path("staff/", accounts_views.staff_list, name="staff_list"),
    path("staff/new/", accounts_views.staff_create, name="staff_create"),
    path("staff/<int:pk>/", accounts_views.staff_detail, name="staff_detail"),
    path("staff/<int:pk>/edit/", accounts_views.staff_edit, name="staff_edit"),
    path("staff/<int:pk>/password/", accounts_views.staff_password, name="staff_password"),
    path("staff/<int:pk>/deactivate/", accounts_views.staff_deactivate, name="staff_deactivate"),
    path("staff/<int:pk>/reactivate/", accounts_views.staff_reactivate, name="staff_reactivate"),
    path("staff/<int:pk>/sessions/<int:session_pk>/end/", accounts_views.session_revoke, name="session_revoke"),
    path("assignments/", staffing_views.overview, name="assignments"),
    path("assignments/subject/<int:class_subject_pk>/assign/", staffing_views.assign, name="assignment_add"),
    path("assignments/<int:pk>/end/", staffing_views.end, name="assignment_end"),
    path("assignments/class/<int:class_pk>/class-teacher/", staffing_views.class_teacher, name="class_teacher_set"),
    path("assignments/<int:assignment_pk>/cover/", staffing_views.cover, name="cover_add"),
    path("assignments/cover/<int:pk>/revoke/", staffing_views.cover_revoke, name="cover_revoke"),
    path("audit/", core_views.audit_log, name="audit_log"),
    path("setup/", admin.site.urls),
]
