from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.shortcuts import render

from .models import AuditLog


@login_required
@permission_required("core.view_audit_log", raise_exception=True)
def audit_log(request):
    logs = AuditLog.objects.select_related("actor")
    action = request.GET.get("action", "").strip()
    who = request.GET.get("who", "").strip()
    obj = request.GET.get("object", "").strip()
    if action:
        logs = logs.filter(action__startswith=action)
    if who:
        logs = logs.filter(actor__username__icontains=who)
    if obj:
        kind, _, pk = obj.partition(":")
        logs = logs.filter(object_type=kind, **({"object_id": pk} if pk.isdigit() else {}))
    page = Paginator(logs, 50).get_page(request.GET.get("page"))
    return render(request, "audit/list.html", {"page": page, "action": action, "who": who, "object": obj})
