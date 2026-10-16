"""Host-namespace root ABI observed in composed4726 METER-host(-DETAIL)."""
import pytest
from cps.services.reflow import native_resources as resources

pytestmark = pytest.mark.unit


def _host_layout(tmp_path):
    proc, mount = tmp_path/'proc', tmp_path/'cgroup'
    (proc/'self').mkdir(parents=True)
    membership = '/docker/f33c1b2ebfc267258e46fcbf851130b8b6a55b1f3ef5f279feca00377f15182a'
    leaf = mount/membership.lstrip('/')
    leaf.mkdir(parents=True)
    (proc/'meminfo').write_text('MemAvailable:    2380368 kB\n')
    (proc/'self/cgroup').write_text('0::'+membership+'\n')
    escaped = str(mount).replace(' ', r'\040')
    row = f'321 320 0:39 / {escaped} ro,nosuid,nodev,noexec,relatime - cgroup2 cgroup rw\n'
    (proc/'self/mountinfo').write_text(row)
    controllers = 'cpuset cpu io memory hugetlb pids rdma\n'
    (mount/'cgroup.controllers').write_text(controllers)
    (mount/'cgroup.subtree_control').write_text(controllers)
    # Actual root lacks memory.max/current and cgroup.type/events.
    for node, used in ((leaf, '24993792'), (leaf.parent, '1283751936')):
        (node/'memory.current').write_text(used)
        (node/'memory.max').write_text('max')
        (node/'cgroup.type').write_text('domain')
    return proc, mount, leaf, row, membership


def test_actual_host_root_without_nonroot_files(tmp_path):
    proc, _, _, _, _ = _host_layout(tmp_path)
    assert resources.linux_headroom(proc) == 2380368 * 1024


@pytest.mark.parametrize('subtree_first', [False, True])
def test_root_absence_does_not_hide_visible_ancestor(tmp_path, subtree_first):
    proc, mount, leaf, row, membership = _host_layout(tmp_path)
    (leaf.parent/'memory.max').write_text(str(1283751936 + 1024**3))
    view = tmp_path/'subtree'
    view.mkdir()
    (view/'memory.max').write_text('max')
    (view/'memory.current').write_text('24993792')
    escaped = str(view).replace(' ', r'\040')
    subrow = f'322 320 0:39 {membership} {escaped} ro - cgroup2 cgroup rw\n'
    (proc/'self/mountinfo').write_text(subrow+row if subtree_first else row+subrow)
    assert resources.linux_headroom(proc) == 1024**3
    # Neither domain type nor available controllers makes a subtree the root.
    (view/'memory.max').unlink()
    (view/'memory.current').unlink()
    (view/'cgroup.type').write_text('domain')
    (view/'cgroup.controllers').write_text('memory')
    with pytest.raises((OSError, ValueError)):
        resources.linux_headroom(proc)


@pytest.mark.parametrize('fault', ['missing-controllers', 'no-memory', 'partial-pair', 'v1'])
def test_root_layout_unknowns_still_fail_closed(tmp_path, fault):
    proc, mount, _, _, _ = _host_layout(tmp_path)
    if fault == 'missing-controllers':
        (mount/'cgroup.controllers').unlink()
    elif fault == 'no-memory':
        (mount/'cgroup.controllers').write_text('cpu io')
    elif fault == 'partial-pair':
        (mount/'memory.current').write_text('0')
    else:
        with (proc/'self/cgroup').open('a') as stream:
            stream.write('4:memory:/docker/example\n')
    with pytest.raises((OSError, ValueError)):
        resources.linux_headroom(proc)
