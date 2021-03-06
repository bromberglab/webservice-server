import threading
from pathlib import Path
from django.conf import settings
import os
import re
import shutil
from shutil import move as stupid_broken_move
import random
import string
from .images import update_file_types
from .models import Globals
import subprocess
import json
from app.events import send_event
from app.util import now, dtformat
from datetime import datetime
from django.utils.timezone import make_aware

base_path = Path(settings.DATA_PATH)
base_path /= "data"
chunk_suffix = ".partial_chunk"
chunk_suffix_done = chunk_suffix + "_done"

# valid delimiters to separate job-name from file-name.
# i.e. job1-file.txt, job1_file.txt, etc.
delimiters = "([,_.\s-])"


def fixed_move_that_fixes_the_super_stupid_annoying_bug_that_pathlib_has(
    src, *args, **kwargs
):
    """ pathlib's move fails with a path argument as src sometimes """
    src = str(src)
    if src[-1] == "/":
        src = src[:-1]
    return stupid_broken_move(src, *args, **kwargs)


# shorter alias
move = fixed_move_that_fixes_the_super_stupid_annoying_bug_that_pathlib_has


def handle_uploaded_file(request):
    """ stores a chunk of an uploaded file """
    data = request.data
    upload = get_upload(request)

    file = data["file"]
    chunkNumber = data["chunkNumber"]
    totalChunks = int(data["totalChunks"])
    uuid = str(upload.uuid)
    relativePath = data["relativePath"]
    filename = data["filename"]
    file_type = "file"

    relativePath = Path(relativePath)
    rnd = Globals().instance.random
    relativePath = relativePath.parent / (rnd + "_" + relativePath.name)

    save_file(
        upload,
        file,
        base_path / file_type / uuid / relativePath / chunkNumber,
        totalChunks,
        filename,
    )


def list_all_files(path, type=None, id=None, relative=True, only_full_uploads=True):
    """
    returns an array of path objects for all files in a directory.
    path: One way to set the path. Alternative is to specify type and id.
    If relative: removes the suffix `path`.
    If only_full_uploads: Filters out partial chunks.
    """

    if path is None:
        path = base_path
        path /= type
        path /= str(id)

    files = [os.path.join(dp, f) for dp, dn, fn in os.walk(path) for f in fn]
    files = [Path(f) for f in files]
    if only_full_uploads:
        files = [
            f
            for f in files
            if not (f.match("*" + chunk_suffix) or f.match("*" + chunk_suffix_done))
        ]

    if relative:
        files = [os.path.relpath(f, path) for f in files]
        files = [Path(f) for f in files]

    return files


def path_matches_any(f: Path, suffixes):
    """ true, if any of the suffixes match the path f """

    for s in suffixes:
        if f.match(s):
            return True

    return False


def list_cleanup_files(type, id, relative=True):
    """ returns an array of files to be deleted, i.e. partial chunks """

    files = list_all_files(None, type, id, relative=relative, only_full_uploads=False)

    suffixes = [
        "*" + chunk_suffix,
        "*" + chunk_suffix_done,
        "*.DS_Store",
        "**/__MACOSX/**",
    ]
    files = [f for f in files if path_matches_any(f, suffixes)]

    return files


def to_file_tree(files):
    """ turns a list of files into a vue-compatible tree structure """
    tree = []

    for f in files:
        t = tree
        for p in f.parts[:-1]:
            t_ = [i for i in t if i["name"] == p]
            if len(t_):
                t_ = t_[0]
            else:
                t_ = {"name": p, "children": []}
                t.append(t_)
            t = t_["children"]
        t.append({"name": f.parts[-1]})

    return tree


def file_tree(type, id):
    """ convenience method to return the tree structure of an upload """
    files = list_all_files(None, type, id)

    return to_file_tree(files)


def save_file(upload, file, path: Path, totalChunks=0, filename="file"):
    """ handles a single chunk after upload, and re-assambles finished uploads """
    path = path.with_suffix(chunk_suffix)
    done_path = path.with_suffix(chunk_suffix_done)
    os.makedirs(path.parent, exist_ok=True)
    with open(path, "wb+") as destination:
        for chunk in file.chunks():
            destination.write(chunk)
    done_path.touch()

    num_files = len([f for f in os.listdir(path.parent)])

    # x2 because every chunk should have a partial file and a completion flag
    if num_files == totalChunks * 2:
        reassemble(upload, path, filename, totalChunks)


def reassemble(upload, path, filename, totalChunks):
    from threading import Thread

    Thread(
        target=reassemble_threaded, args=(upload, path, filename, totalChunks)
    ).start()


def reassemble_threaded(upload, path, filename, totalChunks):
    upload.reassembling = True
    upload.save()
    with open(path.parent.parent / filename, "wb+") as wfd:
        for i in range(totalChunks):
            partial_path = path.parent / str(i + 1)  # chunk numbering starts with 1
            partial_path = partial_path.with_suffix(chunk_suffix)
            partial_path_done = partial_path.with_suffix(chunk_suffix_done)

            with open(partial_path, "rb+") as fd:
                shutil.copyfileobj(fd, wfd)
            os.remove(partial_path)
            os.remove(partial_path_done)
        os.rmdir(path.parent)

    upload.reassembling = False
    upload.save()
    send_event("reassembled", {"uuid": upload.uuid})


def get_upload(request):
    from .models import Upload

    """ every request has an upload associated to it via the session. Not thread safe """

    pk = request.session.get("upload_pk", None)
    upload = None
    if pk is not None:
        try:
            upload = Upload.objects.get(pk=pk)
        except:
            pass  # upload is still None
    if upload is None or upload.is_finished:
        upload = Upload()
        if request.user.is_authenticated:
            upload.user = request.user
        upload.save()
        pk = upload.pk
        request.session["upload_pk"] = str(pk)

    return upload


def list_dirs(path):
    """ return a non-recursive list of directories in a folder """
    path = Path(path)
    files = os.listdir(path)
    files = [f for f in files if os.path.isdir(path / f)]

    return files


def list_files(path):
    """ return a non-recursive list of files in a folder """
    path = Path(path)
    files = os.listdir(path)
    files = [f for f in files if os.path.isfile(path / f)]

    return files


def filter_start(items, prefix):
    """ from a list of strings, return those that start with prefix """
    items = filter(lambda i: i.startswith(prefix), items)

    return list(items)


def find_prefix(splits, files):
    """
    Tries to guess a common prefix for files in a directory,
    with a single file as a basis.
    splits: Pre-split filename, split according to a list of delimiters. The
            list should include all delimiters as separate entities.
            abc.txt would become [abc . txt]
    files: All files in this directory.
    """
    n = len(files)
    files.sort()
    prev_prefix = prefix = "".join(splits[:1])
    splits = splits[1:]

    while (
        len(filter_start(files, prev_prefix)) == n
        or len(filter_start(files, prefix)) > 1
    ) and len(splits):
        prev_prefix = prefix
        prefix += "".join(splits[:2])
        splits = splits[2:]

    return prev_prefix


def get_prefixes(files):
    """
    Tries to guess all common prefixes for files in a directory.
    If a files matches multiple prefixes, this method isn't useful.

    examples (tests):
    >>> ["a.txt", "a.pdf", "b.txt", "b.pdf"]
    {'a': ['a.pdf', 'a.txt'], 'b': ['b.pdf', 'b.txt']}

    files: All files in this directory.

    returns: A dictionary with prefixes as keys and found files as values.
    """
    matched = {}
    unmatched = [f for f in files]

    while len(unmatched):
        file = unmatched[0]
        splits = re.split(delimiters, file)

        prefix = find_prefix(splits, files)

        matched[prefix] = []
        for i in filter_start(unmatched, prefix):
            matched[prefix].append(i)
            unmatched.remove(i)

    return matched


def unwrap_path(path):
    """
    As long as a path contains only a single directory, add
    the subdirectory to the path and continue recursively.
    """

    dirs = list_dirs(path)
    files = list_files(path)

    if len(dirs) == 1 and len(files) == 0:
        return unwrap_path(path / dirs[0])
    return path


def untar_upload(upload, path, files):
    from threading import Thread

    Thread(target=threaded_extract_upload, args=(un_tar, upload, path, files)).start()


def unzip_upload(upload, path, files):
    from threading import Thread

    Thread(target=threaded_extract_upload, args=(un_zip, upload, path, files)).start()


def threaded_extract_upload(fun, upload, path, files):
    if len(files) == 1:
        fun(path / files[0], make_folder=False)
    else:
        for f in files:
            fun(path / f, make_folder=True)
    upload.extracting = False
    upload.save()
    send_event("extracted", {"uuid": upload.uuid})


def finish_upload_(request, upload):
    """
    Returns the format structure of an upload after it is finished.

    returns:
     tree: vue-compatible tree structure of the format.
     files: list of path objects of the format.
     suffixes: list of string objects of the format (no prefix).
     dirs: list of directories that are part of the format.
     prefixes: returned by get_prefixes.
     error: False, or an error message string.
            In case of an error, the other outputs might be unusable.
    """

    error = lambda e: ([], [], [], [], [], e)
    uuid = str(upload.uuid)
    path = base_path
    path /= "file"
    path /= uuid

    for f in list_cleanup_files("file", str(upload.uuid), relative=False):
        os.remove(f)
        try:
            os.rmdir(f.parent)
            # is_empty = True
        except OSError:
            pass
            # is_empty = False

    path = unwrap_path(path)
    dirs = list_dirs(path)
    files = list_files(path)

    all_tar = None
    if len(dirs) == 0:
        for f in files:
            if all_tar != False and f.endswith(".tar.gz"):
                all_tar = True
            else:
                all_tar = False
    if all_tar:
        extract = request.data.get("extract", None)
        if not upload.no_extract:
            if extract is None:
                return error("extract")
            if extract:
                upload.extracting = True
                upload.save()
                untar_upload(upload, path, files)
                return error("extracting")
            upload.no_extract = True
            upload.save()
    all_zip = None
    if len(dirs) == 0:
        for f in files:
            if all_zip != False and f.endswith(".zip"):
                all_zip = True
            else:
                all_zip = False
    if all_zip:
        extract = request.data.get("extract", None)
        if not upload.no_extract:
            if extract is None:
                return error("extract")
            if extract:
                upload.extracting = True
                upload.save()
                unzip_upload(upload, path, files)
                return error("extracting")
            upload.no_extract = True
            upload.save()
    if request.data.get("extract_only", False):
        return error("no extract")

    if len(files) + len(dirs) == 0:
        return error("Nothing uploaded.")

    prefixes = {}
    suffixes = []
    if len(files) > 0:
        # process files
        prefixes = get_prefixes(files)
        prefix = None, 0
        for k, v in prefixes.items():
            if len(v) > prefix[1]:
                prefix = k, len(v)

        prefix = prefix[0]
        files = []
        suffixes = []
        for f in prefixes[prefix]:
            suffixes.append(f[len(prefix) :])
            files.append(Path("<job>" + f[len(prefix) :]))

        suffixes = [re.sub("^" + delimiters + "+", "", s) for s in suffixes]

    if len(dirs) > 0:
        # process directories
        found = -1, None
        for d in dirs:
            dir_files = list_all_files(path / d)
            if len(dir_files) > found[0]:
                found = len(dir_files), dir_files

        dir_files = found[1]
        json_dump = json.dumps([str(f) for f in dir_files])
        dirs = list(
            filter(
                lambda i: json.dumps([str(f) for f in list_all_files(path / i)])
                == json_dump,
                dirs,
            )
        )

        suffixes += [str(f).split("/")[-1] for f in dir_files]
        files += ["<job>" / f for f in dir_files]

    if len(dirs) == 0 or len(prefixes) == 0:
        # no matching needed, return
        return (
            to_file_tree(files),
            files,
            suffixes,
            dirs,
            prefixes,
            False,
        )

    for d in dirs:
        if d not in list(prefixes.keys()):
            return error(
                (
                    "Parsing failed. We found a folder called '%s' that was not part of "
                    "any of the jobs we found. If an upload contains both folders and "
                    "files, we try to match them. If the jobs were not correctly detected, "
                    "try following the best practices for naming.\n\nFound jobs:\n%s"
                )
                % (d, "\n".join(prefixes.keys()))
            )
    for d in prefixes.keys():
        if d not in dirs:
            return error(
                (
                    "Parsing failed. We found a job called '%s' that was not part of "
                    "any of the folders we found. If an upload contains both folders and "
                    "files, we try to match them. If the jobs were not correctly detected, "
                    "try following the best practices for naming. Folders that don't have "
                    "all necessary files are ignored.\n\nFound folders:\n%s"
                )
                % (d, "\n".join(dirs))
            )

    # all jobs matched
    return (
        to_file_tree(files),
        files,
        suffixes,
        dirs,
        prefixes,
        False,
    )

    return error("Format not supported right now.")


def finish_upload(request, upload):
    tree, files, suffixes, dirs, prefixes, error = finish_upload_(request, upload)

    return {"tree": to_file_tree(files), "suffixes": suffixes, "error": error}


sub_uploads = {}


def move_file(
    path,
    upload_id,
    file,
    type,
    job,
    type_id=0,
    copy=False,
    remove_prefix=False,
    duplicates=None,
):
    """
    For a file from an uploaded structure,
    move or copy it to the right location.

    path: (unwrapped) path of the upload.
    file: slash-separated sub path of the file.
    if remove_prefix: replace `job` at the begginning of the
                      file name with 'file'.
    """
    from .models import Upload

    global sub_uploads
    assert isinstance(file, str)
    assert not ".." in file, "Illegal sequence: .."
    assert not ".." in job, "Illegal sequence: .."
    assert not ".." in type, "Illegal sequence: .."

    duplicate = False
    if duplicates is not None and type in duplicates:
        duplicate = True

    # For every type and type_id, get the correct child-upload
    sub_uploads = sub_uploads[upload_id] = sub_uploads.get(upload_id, {})
    upload = sub_uploads.get(type + str(type_id), None)
    if upload is None:
        parent_upload = Upload.objects.get(pk=upload_id)
        assert not ".." in parent_upload.display_name, "Illegal sequence: .."

        if duplicate:
            name = parent_upload.display_name + " " + str(type_id)
        else:
            name = parent_upload.display_name

        upload = sub_uploads[type + str(type_id)] = Upload(
            file_type=type,
            name=name,
            is_finished=True,
            is_newest=True,
            user=parent_upload.user,
        )
        upload.save()

    from_path = path / file

    if remove_prefix:
        file = file.split("/")
        if file[-1].startswith(job):
            file[-1] = "file" + file[-1][len(job) :]
        file = "/".join(file)

    to_path = base_path / type / str(upload.pk) / (job + ".job") / file

    os.makedirs(to_path.parent, exist_ok=True)

    if copy:
        shutil.copy(from_path, to_path)
    else:
        move(from_path, to_path)

    upload.calc_size()


def finalize_upload(request, upload):
    """ move files according to the format annotations from the user """
    uuid = str(upload.uuid)
    tree, files, suffixes, dirs, prefixes, error = finish_upload_(request, upload)

    if error != False:
        return {"error": error}

    data = request.data
    manual_format = data.get("manual_format", False)
    wrap_files = data.get("wrap_files", False)
    checkboxes = data.get("checkboxes", [])
    types = data.get("types", [])
    for t in types:
        assert not ".." in t, "Illegal sequence: .."

    len_suffixes = len(suffixes)
    len_types = len(types)

    # the tree consists of one <job> folder at the end,
    # all other entries are the files.
    num_files = len(tree) - (1 if len(dirs) else 0)
    num_dirs = len(files) - num_files

    # avoid name duplicate if 'file' is part of the types
    path = base_path / "file" / (uuid + "_")
    move(base_path / "file" / uuid, path)
    if not manual_format:
        path = unwrap_path(path)

    if not manual_format:
        types_ = []
        duplicates = []
        for t in types:
            if t in types_:
                duplicates.append(t)
            types_.append(t)

        for prefix, files in prefixes.items():
            for file in files:
                # we want the longest suffix that the file matches.
                longest_find = 0, None
                for i in range(num_files):
                    if file.endswith(suffixes[i]):
                        if len(suffixes[i]) > longest_find[0]:
                            longest_find = len(suffixes[i]), i
                i = longest_find[1]

                for t in checkboxes[i]:
                    type = types[t]
                    move_file(
                        path,
                        uuid,
                        file,
                        type,
                        type_id=t,
                        job=prefix,
                        copy=(t != checkboxes[i][-1]),
                        remove_prefix=True,
                        duplicates=duplicates,
                    )
        for dir in dirs:
            for i in range(num_dirs):
                i += (
                    num_files  # suffixes is files and dirs, add an offset to the index.
                )
                for t in checkboxes[i]:
                    type = types[t]
                    move_file(
                        path / dir,
                        uuid,
                        suffixes[i],
                        type,
                        type_id=t,
                        job=dir,
                        copy=(t != checkboxes[i][-1]),
                    )

        try:
            shutil.rmtree(base_path / "file" / (uuid + "_"))
        except:
            pass
        upload.delete()
    else:
        # manual format, just specify the file type.

        assert not ".." in manual_format, "Illegal sequence: .."
        upload.file_type = manual_format
        upload.is_finished = True
        upload.save()

        to_path = base_path
        to_path /= upload.file_type
        to_path /= str(upload.uuid)
        if wrap_files:
            to_path /= "static"
        if path != to_path:
            os.makedirs(to_path.parent, exist_ok=True)
            move(path, to_path)
        try:
            shutil.rmtree(base_path / "file" / (uuid + "_"))
        except:
            pass

        upload.finished_at = now()
        upload.calc_size()

    update_file_types()
    return 0


def copy_folder(inp_path, out_path):
    assert not ".." in inp_path, "Illegal sequence: .."
    assert not ".." in out_path, "Illegal sequence: .."

    path = Path(settings.DATA_PATH)
    for p in inp_path.split("/"):
        path /= p
    inp_path = path
    path = Path(settings.DATA_PATH)
    for p in out_path.split("/"):
        path /= p
    out_path = path

    os.makedirs(inp_path, exist_ok=True)
    os.makedirs(out_path, exist_ok=True)
    shutil.rmtree(out_path)

    shutil.copytree(inp_path, out_path)


def un_tar(file, make_folder=False, remove=True):
    file = Path(file)

    if make_folder:
        name = file.name[: -len(".tar.gz")]
        os.makedirs(file.parent / name, exist_ok=True)
        move(file, file.parent / name / file.name)
        file = file.parent / name / file.name

    subprocess.run(["tar", "-kxzf", str(file), "-C", str(file.parent)])

    if remove:
        os.remove(file)


def un_zip(file, make_folder=False, remove=True):
    file = Path(file)

    if make_folder:
        name = file.name[: -len(".zip")]
        os.makedirs(file.parent / name, exist_ok=True)
        move(file, file.parent / name / file.name)
        file = file.parent / name / file.name

    subprocess.run(["unzip", "-n", str(file), "-d", str(file.parent)])

    if remove:
        os.remove(file)


def make_download_link(rel_path, name="download"):
    import random
    import string
    from .models import Download

    from_path = base_path
    from_path /= rel_path

    to_path = settings.DOWNLOADS_DIR
    os.makedirs(to_path, exist_ok=True)

    folder = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    to_path = os.path.join(to_path, folder)

    subprocess.run(["ln", "-s", str(from_path), to_path])

    Download(path=to_path).save()

    return folder


def clear_my_upload(request):
    upload = get_upload(request)
    uuid = str(upload.uuid)
    path = base_path / "file" / uuid

    try:
        shutil.rmtree(path)
    except:
        pass


def clear_upload(upload):
    uuid = str(upload.uuid)
    path = base_path / upload.file_type / uuid

    try:
        shutil.rmtree(path)
    except:
        pass


def clean_job(job):
    id = str(job.pk)
    path = base_path / "job_outputs" / id

    try:
        shutil.rmtree(path)
    except:
        pass


def clear_logs():
    path = Path(settings.DATA_PATH) / "logs"
    files = list_all_files(path, relative=False, only_full_uploads=False)

    for f in files:
        file_name = f.name
        time_name = file_name[:-4]
        time = make_aware(datetime.strptime(time_name, dtformat))

        if (now() - time).total_seconds() > 60 * 60 * 24 * 7:  # 7d
            os.remove(f)

            p = f.parent
            while len(list_dirs(p)) == 0 and len(list_files(p)) == 0:
                os.rmdir(p)
                p = p.parent


def logs_for(name):
    path = Path(settings.DATA_PATH) / "logs"
    for p in str(name).split("-"):
        path /= p

    result = ""

    files = list_all_files(path, only_full_uploads=False)
    for f in files:
        with open(path / f, "r") as handle:
            text = handle.read()
        dir_name = str(f.parent.parent).replace("/", "-")
        file_name = f.name
        file_name = file_name.split(".")[0]
        name = "%s [%s]:" % (dir_name, file_name)

        result += "%s\n%s\n" % (name, text)

    if result == "":
        return "Logs are kept for 7 days."

    return result


def calc_size(*a):
    path = Path(settings.DATA_PATH) / "data"
    for p in a:
        path /= p

    s = subprocess.run(["du", "-ks", str(path)], capture_output=True).stdout.decode()
    s = s.split("\t")[0].replace(" ", "")
    return int(s) * 1024.0


def calc_usage():
    if settings.DEBUG:
        return "0%"
    path = Path(settings.DATA_PATH)

    s = subprocess.run(
        ["sh", "-c", 'df "' + str(path) + "\" | tail -1 | grep -Eo '[0-9]+\%'"],
        capture_output=True,
    ).stdout.decode()
    return s.replace("\n", "")

