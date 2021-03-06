import os
import time
from django.shortcuts import render, redirect
from django.views import View as RegView
from django.http import HttpResponse, Http404, HttpResponseRedirect
from django.conf import settings
from django.urls import reverse
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.status import *
from rest_framework.views import APIView
from rest_framework.parsers import FileUploadParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.generics import ListAPIView
from django.contrib.auth.models import User, Permission
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView as BaseLoginView
from django.contrib.auth.password_validation import validate_password
from django.utils.decorators import method_decorator
from django_zip_stream.responses import FolderZipResponse
from django.contrib.auth import authenticate, login, logout
from django.core.validators import validate_email
from django.utils import timezone

# Create your views here.

from .models import *
from .files import (
    handle_uploaded_file,
    get_upload,
    file_tree,
    finish_upload,
    finalize_upload,
    clear_my_upload,
    clear_upload,
)
from .serializers import *
from .events import send_event
from .util import NoGuestPermission, IsSuperuser


@login_required
def login_index_view(request):
    return Response()


class IndexView(APIView):
    def get(self, request, format=None):
        if request.headers.get("User-Agent", "").startswith("GoogleHC"):
            return Response(status=HTTP_200_OK)
        if settings.DEBUG:
            return redirect("/api/admin")

        if not request.user.is_authenticated:
            return login_index_view(request)

        return redirect("/")


class AdminCreationView(APIView):
    def get(self, request, format=None):
        import random
        import string

        try:
            User.objects.get(username="admin")
            return Response("User exists")

        except User.DoesNotExist:
            a = User(username="admin", email="admin@localhost")
            pw = "".join(
                random.choices(string.ascii_lowercase + string.digits + "!+-._", k=24)
            )
            a.set_password(pw)
            a.is_superuser = True
            a.is_staff = True
            a.save()
            return Response("admin:" + pw)


class WorkflowStorageView(APIView):
    permission_classes = [NoGuestPermission]

    def get(self, request, format=None):
        name = request.GET.get("name", "")
        pk = request.GET.get("pk", "")
        if pk:
            try:
                flow = Workflow.objects.get(pk=pk)
            except:
                return Response(status=HTTP_404_NOT_FOUND)
            assert (
                flow.is_shared or request.user.is_superuser or flow.user == request.user
            )
        else:
            try:
                if name.lower().startswith("api/"):
                    name = name[4:]
                    flow = ApiWorkflow.objects.get(uuid=name)
                else:
                    flow = Workflow.objects.get(should_run=False, name=name)
            except:
                return Response(status=HTTP_404_NOT_FOUND)

        return Response(flow.json)

    def post(self, request, format=None):
        name = request.data.get("name", "")
        try:
            flow = Workflow.objects.get(should_run=False, name=name)
        except:
            flow = Workflow(name=name)
        flow.json = request.data.get("data", dict())
        flow.user = request.user
        flow.save()

        return Response()


class WorkflowView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, name, format=None):
        try:
            flow = Workflow.objects.get(pk=name)
        except:
            return Response(status=HTTP_404_NOT_FOUND)
        if (
            not flow.is_shared
            and flow.user != request.user
            and not request.user.is_superuser
        ):
            return Response(status=HTTP_403_FORBIDDEN)

        serializer = WorkflowSerializer(flow)

        return Response(serializer.data)

    def delete(self, request, name, format=None):
        try:
            flow = Workflow.objects.get(pk=name)
        except:
            return Response(status=HTTP_404_NOT_FOUND)
        if flow.user != request.user and not request.user.is_superuser:
            return Response(status=HTTP_403_FORBIDDEN)

        flow.delete()

        return Response()


class WorkflowShareView(APIView):
    permission_classes = [NoGuestPermission]

    def post(self, request, format=None):
        pk = request.data.get("pk" "")
        try:
            flow = Workflow.objects.get(pk=pk)
        except:
            return Response(status=HTTP_404_NOT_FOUND)
        if flow.user != request.user and not request.user.is_superuser:
            return Response(status=HTTP_403_FORBIDDEN)

        flow.is_shared = True
        flow.save()

        return Response()


class WorkflowsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, format=None):
        from django.db.models import Q

        if request.user.is_superuser:
            flows = Workflow.objects.all()
        else:
            flows = Workflow.objects.filter(Q(user=request.user) | Q(should_run=False))
        serializer = WorkflowSerializer(flows, many=True)

        return Response(serializer.data)


class JobView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, format=None):
        name = request.GET.get("name", "")
        try:
            job = Job.objects.get(uuid=name)
        except:
            return Response(status=HTTP_404_NOT_FOUND)

        job = JobSerializer(job)

        return Response(job.data)


class JobLogsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, format=None):
        name = request.GET.get("name", "")
        as_json = request.GET.get("json", False)

        try:
            job = Job.objects.get(uuid=name)
        except:
            return Response(status=HTTP_404_NOT_FOUND)
        logs = job.logs

        if as_json:
            return Response(logs)
        else:
            response = HttpResponse(logs, content_type="text/plain")
            response["Content-Disposition"] = "attachment; filename=%s.log" % name
            return response


class WorkflowRunView(APIView):
    permission_classes = [NoGuestPermission]

    def post(self, request, format=None):
        flow = Workflow(should_run=True, user=request.user)
        flow.json = request.data.get("data", dict())
        flow.prepare_workflow()

        return Response(flow.pk)


class WorkflowNameView(APIView):
    permission_classes = [NoGuestPermission]

    def post(self, request, format=None):
        old = request.data.get("pk", "")
        new = request.data.get("name", "")

        try:
            flow = Workflow.objects.get(pk=old)
        except:
            return Response(status=HTTP_404_NOT_FOUND)
        if request.user.is_superuser or flow.user == request.user:
            flow.name = new
            flow.save()

        return Response()


class ListImagesView(ListAPIView):
    permission_classes = [IsAuthenticated]
    queryset = NodeImage.objects.all()
    serializer_class = NodeImageSerializer


class ListUploadsView(ListAPIView):
    permission_classes = [NoGuestPermission]
    serializer_class = UploadSerializer

    def get_queryset(self):
        user = self.request.user

        query = Upload.objects.filter(is_finished=True)
        if user.is_superuser:
            return query
        return query.filter(user=user)


class UploadView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, uuid, format=None):
        user = self.request.user

        upload = Upload.objects.get(uuid=uuid)
        if not user.is_superuser and upload.user != user:
            return Response(status=HTTP_403_FORBIDDEN)
        clear_upload(upload)
        upload.delete()
        return Response()


class InspectImageView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, name, format=None):
        try:
            image = NodeImage.objects.get(name=name)
        except:
            return Response(status=HTTP_404_NOT_FOUND)
        tags = image.tag_refs.all()
        tags = [{"name": i.name, "sha": i.sha} for i in tags]
        return Response(
            {
                "name": name,
                "labels": image.labels,
                "cmd": image.cmd,
                "env": image.env,
                "tags": tags,
            }
        )


class ServerStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, format=None):
        from app.files import calc_usage
        from filecmp import cmp

        outdated = not cmp(
            settings.BASE_DIR + "/.commit", settings.BASE_DIR + "/.commit.online"
        )

        return Response({"disk": calc_usage(), "outdated": outdated})


class ImportImageView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, format=None):
        from .images import import_image

        name = request.data.get("name", "")
        tag = request.data.get("tag", "")

        if tag:
            import_image(name, tag, user=request.user)
        else:
            import_image(name, user=request.user)

        send_event("image-imported", name)

        return Response()


class UpdateImageView(APIView):
    permission_classes = [NoGuestPermission]

    def post(self, request, format=None):
        from .images import update_image

        name = request.data.get("name", "")

        update_image(name, user=request.user)

        send_event("image-imported", name)

        return Response()


class ChangeImageView(APIView):
    permission_classes = [NoGuestPermission]

    def post(self, request, format=None):
        from .images import change_image

        name = request.data.get("name", "")
        data = request.data.get("data", {})

        change_image(name, data=data, user=request.user)

        send_event("image-imported", name)

        return Response()


class DeleteImageView(APIView):
    permission_classes = [NoGuestPermission]

    def delete(self, request, format=None):
        from .images import import_image

        name = request.data.get("name", "")
        try:
            image = NodeImage.objects.get(name=name)
        except:
            return Response(status=HTTP_404_NOT_FOUND)
        if image.imported_by == request.user or request.user.is_superuser:
            image.delete()
        else:
            Notification.send(
                request.user, "Permissions", "You have insufficient permissions.", 10,
            )
            return Response(status=HTTP_403_FORBIDDEN)

        return Response()


class CommitView(APIView):
    def get(self, request, format=None):
        with open(".commit", "r") as f:
            return Response(f.read().replace("\n", ""))


class CronView(APIView):
    def post(self, request, format=None):
        from app.management.commands.cron import cron

        cron()
        return Response(status=HTTP_200_OK)


class FileUploadView(APIView):
    permission_classes = [NoGuestPermission]
    parser_classes = [MultiPartParser]

    def put(self, request, name=None, format=None):
        handle_uploaded_file(request)

        return Response(status=HTTP_200_OK)


class CheckAuthView(APIView):
    def get(self, request, format=None):
        u = request.user
        return Response(Permissions.from_user(u))


class MyUploadView(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def retrieve(self, request):
        upload = get_upload(request)
        serializer = UploadSerializer(upload)
        return Response(serializer.data)

    def update(self, request):
        upload = get_upload(request)
        serializer = UploadSerializer(upload, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def remove(self, request):
        clear_my_upload(request)
        return Response()


class FinishUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, format=None):
        return Response(finish_upload(request, get_upload(request)))


class FinalizeUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, format=None):
        return Response(finalize_upload(request, get_upload(request)))


class UploadTreeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, format=None):
        upload = get_upload(request)
        tree = file_tree("file", upload.uuid)
        return Response(tree)


class FileTypeListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    queryset = FileType.objects.all()
    serializer_class = FileTypeSerializer


class CreateDownload(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, format=None):
        name = request.data.get("name", "")
        f_type = request.data.get("type", "")
        folder = Upload.for_name(name, f_type).make_download_link()

        url = reverse(
            "download", kwargs={"name": folder, "filename": name.replace("/", "")}
        )
        url = request.build_absolute_uri(url)
        return Response({"url": url})


class DownloadView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, name="", filename="", format=None):

        path = settings.DOWNLOADS_DIR
        path = os.path.join(path, name)

        return FolderZipResponse(
            path, url_prefix=settings.DOWNLOADS_URL, filename=filename
        )


class NotificationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, format=None):
        id = request.GET.get("id", "")
        try:
            n = Notification.objects.get(pk=id)
        except:
            return Response(status=HTTP_404_NOT_FOUND)
        assert n.user == request.user
        n = NotificationSerializer(n).data
        return Response(n)

    def post(self, request, format=None):
        serializer = NotificationSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        n: Notification = serializer.save(user=request.user)
        n.notify()
        return Response(serializer.data)

    def delete(self, request, format=None):
        try:
            n = Notification.objects.get(pk=request.data["pk"])
        except:
            return Response(status=HTTP_404_NOT_FOUND)
        assert n.user == request.user
        n.delete()
        return Response()


class NamesForTypeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, format=None):
        if not request.user.is_authenticated:
            return Response([])

        f_type = request.GET.get("type", "file")

        names = []

        for u in Upload.objects.filter(file_type=f_type, is_newest=True):
            if u.name:
                names.append(u.name)

        return Response(names)


class CookieInfoView(APIView):
    def get(self, request, format=None):
        return Response(request.session.get("show_cookie_info", True))

    def post(self, request, format=None):
        request.session["show_cookie_info"] = False
        return Response(False)


class NotificationsList(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer

    def get_queryset(self):
        user = self.request.user
        return Notification.objects.filter(user=user).order_by("-created_at")[:25]

    def delete(self, request, format=None):
        user = self.request.user
        ns = Notification.objects.filter(user=user)
        for n in ns:
            n.delete()
        return Response()


class LoginOverride(BaseLoginView):
    def get(self, request, *args, **kwargs):
        if request.GET.get("next", None) is None:
            return HttpResponseRedirect(request.path_info + "?next=/")

        return super().get(request, *args, **kwargs)


class RandomNameView(APIView):
    def get(self, request, format=None):
        from .util import default_name

        return Response(default_name())


class ApiWorkflowView(APIView):
    permission_classes = [NoGuestPermission]

    def get(self, request, format=None):
        flows = ApiWorkflow.objects.all()
        if not request.user.is_superuser:
            flows = flows.filter(user=request.user)
        return Response(ApiWorkflowSerializer(flows, many=True).data)

    def delete(self, request, format=None):
        try:
            uuid = request.data.get("uuid", "")
            flow = ApiWorkflow.objects.get(uuid=uuid)
        except:
            return Response(status=HTTP_404_NOT_FOUND)
        if flow.user != request.user and not request.user.is_superuser:
            return Response(status=HTTP_403_FORBIDDEN)

        flow.delete()

        return Response()

    def post(self, request, format=None):
        pk = request.data.get("pk", -1)
        flow = Workflow.objects.get(pk=pk)

        flow = ApiWorkflow(json_string=flow.json_string, user=flow.user)
        inputs, outputs = flow.prepare()
        flow.save()

        return Response({"api-key": flow.pk, "inputs": inputs, "outputs": outputs})


class RunApiWorkflowView(APIView):
    permission_classes = [NoGuestPermission]

    def post(self, request, format=None):
        pk = request.data.get("name", "")
        flow = ApiWorkflow.objects.get(pk=pk)

        for u in Upload.objects.filter(file_type=pk):
            if u.name.startswith("o/"):
                clear_upload(u)
                u.delete()

        for w in Workflow.objects.filter(api_workflow=flow):
            w.delete()

        w_flow = Workflow(
            json_string=flow.json_string,
            user=flow.user,
            should_run=True,
            name="API/%s" % pk,
            api_workflow=flow,
        )
        w_flow.prepare_workflow()
        flow.save()  # update run_at
        return Response({"pk": w_flow.pk, "outputs": flow.outputs_count})


class TokenLoginView(APIView):
    def post(self, request, format=None):
        token = request.data.get("token", "")

        try:
            token = ApiToken.objects.get(token=token)
        except:
            return Response(status=HTTP_400_BAD_REQUEST)

        logout(request)
        login(request, token.user, backend=settings.AUTHENTICATION_BACKENDS[0])
        return Response()


class RestartUpdateView(APIView):
    permission_classes = [IsSuperuser]

    def post(self, request, format=None):
        import os

        os.system("kubectl delete pod -l app=server --force --grace-period=0")


class UpdateResourcesView(APIView):
    permission_classes = [NoGuestPermission]

    def post(self, request, format=None):
        try:
            pk = request.data.get("pk", "")
            workflow = Workflow.objects.get(pk=pk)
        except:
            return Response(status=HTTP_404_NOT_FOUND)

        try:
            result = workflow.update_resources()
            assert result
            api = workflow.api_workflow
            if api is not None:
                api.json_string = workflow.json_string
                api.save()
        except:
            return Response(status=HTTP_400_BAD_REQUEST)
        return Response()


class LoginView(APIView):
    def post(self, request, format=None):
        logout(request)
        try:
            username = request.data.get("username", "")
            password = request.data.get("password", "")

            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)
                return Response({"success": True})

        except:
            pass

        return Response({"success": False})


class LogoutView(APIView):
    def post(self, request, format=None):
        try:
            logout(request)
            return Response({"success": True})
        except:
            pass

        return Response({"success": False})


class GuestLoginView(APIView):
    def post(self, request, format=None):
        logout(request)

        time.sleep(10)

        guest = User.objects.filter(username="guest").first()
        if guest is None:
            guest = User(username="guest")
            guest.save()
            guest.user_permissions.add(Permission.objects.get(codename="is_guest_user"))
            guest.save()

        login(request, guest, backend=settings.AUTHENTICATION_BACKENDS[0])
        request.session["logout_at"] = (
            timezone.now() + timezone.timedelta(days=1)
        ).isoformat()

        return Response({"success": True})


class RegisterView(APIView):
    def post(self, request, format=None):
        logout(request)

        username = request.data.get("username", "admin")
        email = request.data.get("email", "")
        password = request.data.get("password", "")

        if User.objects.filter(username=username).first() is not None:
            return Response({"success": False, "reason": "Username exists."})
        if User.objects.filter(email=email).first() is not None:
            return Response({"success": False, "reason": "E-Mail exists."})
        try:
            validate_email(email)
        except:
            return Response({"success": False, "reason": "E-Mail invalid."})

        user = User(username=username, email=email)
        try:
            validate_password(password, user)
        except Exception as e:
            return Response({"success": False, "reason": " ".join(e)})
        user.set_password(password)
        time.sleep(10)
        user.save()
        user.user_permissions.add(Permission.objects.get(codename="is_guest_user"))
        login(request, user, backend=settings.AUTHENTICATION_BACKENDS[0])

        return Response({"success": True})

