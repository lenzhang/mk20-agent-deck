test:
	shellcheck -s sh -S warning firmware/rescue/*.sh firmware/release/sbin/mk20d firmware/release/share/*.sh tools/mk20-release-build.sh tools/mk20-install.sh tools/mk20-rescue-update.sh
	shellcheck -s bash -S warning tools/tf-identify.sh tools/tf-restore-boot.sh
	python3 -m py_compile tools/mk20_accept.py tools/mk20_soak.py tools/mk20_host_net.py tools/tcp_transport.py
	(cd tools && uv run --with pytest python -m pytest -q test_tcp_transport.py test_mk20_host_net.py test_mk20_soak.py)
	MK20_TEST_BUSYBOX=$${MK20_TEST_BUSYBOX:-$$( [ -x /workspace/tools/bb127/busybox ] && echo /workspace/tools/bb127/busybox )} uv run --with pytest python -m pytest -q tests

release:
	sh tools/mk20-release-build.sh firmware out $$(git rev-parse --short HEAD 2>/dev/null || echo dev)

.PHONY: test release
