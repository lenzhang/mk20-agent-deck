test: sim-test
	shellcheck -s sh -S warning firmware/rescue/*.sh firmware/release/sbin/mk20d firmware/release/share/*.sh tools/mk20-release-build.sh tools/mk20-install.sh tools/mk20-rescue-update.sh tools/mk20-build-bins.sh
	shellcheck -s bash -S warning tools/tf-identify.sh tools/tf-restore-boot.sh
	python3 -m py_compile tools/mk20_key_latency.py tools/mk20_accept.py tools/mk20_soak.py tools/mk20_host_net.py tools/tcp_transport.py tools/tf_gpt_check.py
	(cd tools && uv run --with pytest python -m pytest -q test_tcp_transport.py test_mk20_host_net.py test_mk20_soak.py test_mk20_accept.py test_tf_gpt.py)
	MK20_TEST_BUSYBOX=$${MK20_TEST_BUSYBOX:-$$( [ -x /workspace/tools/bb127/busybox ] && echo /workspace/tools/bb127/busybox )} uv run --with pytest python -m pytest -q tests

bins:   # static armhf bridge + router from source into firmware/release/bin (needs arm-linux-gnueabihf-gcc)
	sh tools/mk20-build-bins.sh

release:
	sh tools/mk20-release-build.sh firmware out $$(git rev-parse --short HEAD 2>/dev/null || echo dev)

sim-test:
	uv run --with pytest python -m pytest -q tests_sim

sim:
	uv run python -m mk20sim --port 8766

.PHONY: test bins release sim-test sim
