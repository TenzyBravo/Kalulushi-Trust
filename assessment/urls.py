from django.urls import path

from . import views

urlpatterns = [
    path("", views.my_subjects, name="assessment_home"),
    path("incomplete/", views.incomplete, name="assessment_incomplete"),
    path("subject/<int:pk>/", views.subject, name="assessment_subject"),
    path("class/<int:pk>/", views.class_results, name="assessment_class_results"),
    path("<int:pk>/", views.marks, name="assessment_marks"),
    path("<int:pk>/save/", views.marks_save, name="assessment_marks_save"),
    path("<int:pk>/learner/<int:learner_pk>/", views.learner_marks, name="assessment_learner"),
    path("competencies/<int:pk>/", views.competencies, name="assessment_competencies"),
    path("competencies/sheet/<int:sheet_pk>/learner/<int:learner_pk>/", views.competency_learner, name="assessment_competency_learner"),
]
