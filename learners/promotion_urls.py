from django.urls import path

from . import promotion_views as views

urlpatterns = [
    path("", views.index, name="promotions"),
    path("class/<int:pk>/", views.sheet, name="promotion_sheet"),
    path("approvals/", views.approvals, name="promotion_approvals"),
    path("approvals/sheet/<int:pk>/", views.review, name="promotion_review"),
    path("transfer/<int:pk>/decide/", views.transfer_decide, name="transfer_decide"),
    path("held/<int:pk>/", views.place, name="promotion_place"),
]
