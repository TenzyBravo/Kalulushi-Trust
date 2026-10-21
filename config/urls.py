from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from accounts.views import home

admin.site.site_header = "School administration"

urlpatterns = [
    path("", home, name="home"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("pupils/", include("people.urls")),
    path("admin/", admin.site.urls),
]
