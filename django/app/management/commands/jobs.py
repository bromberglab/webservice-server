import json
import time
import random
import string
from app.models import *
from app.images import *
from kubernetes import client, config, watch


def get_status(pk):
    api = client.CoreV1Api()

    # setup watch
    w = watch.Watch()
    status = 'failed'
    pod = None
    for event in w.stream(api.list_pod_for_all_namespaces, timeout_seconds=0):
        if event['object'].metadata.labels.get('job-name', None) == str(pk):
            pod = event['object'].metadata.name
            if event['type'] == 'MODIFIED':
                status = event['object'].status.phase.lower()
                if status in ['succeeded', 'failed']:
                    break
    w.stop()

    return status, pod


def launch_job(job):
    dep = json.loads(job.body)

    dep['metadata']['name'] = str(job.pk)
    k8s_batch_v1 = client.BatchV1Api()
    resp = k8s_batch_v1.create_namespaced_job(body=dep, namespace="default")


def delete_job(name, pod):
    k8s_batch_v1 = client.BatchV1Api()
    k8s_v1 = client.CoreV1Api()
    resp = k8s_batch_v1.delete_namespaced_job(str(name), namespace='default')
    resp = k8s_v1.delete_namespaced_pod(str(pod), namespace='default')


def run_job(job):
    rnd = ''.join(random.choices(string.ascii_lowercase + string.digits, k=5))

    job.status = rnd
    job.scheduled = True
    job.save()

    # break race condition:
    time.sleep(0.1)
    job = Job.objects.get(pk=job.pk)
    if job.status != rnd:
        return

    # configure client
    config.load_kube_config()

    launch_job(job)
    job.status, pod = get_status(job.pk)

    job.finished = True
    job.save()
    delete_job(job.pk, pod)


def cron():
    glob = Globals().instance

    while True:
        job = Job.objects.filter(scheduled=False).first()
        if job is None:
            break

        run_job(job)