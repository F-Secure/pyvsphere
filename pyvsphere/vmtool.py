#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Simple demonstration script for VIM bulk operations
# Wants to be a useful tool when it grows up.
#
# Copyright 2011 F-Secure Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import os
import optparse
import time

from pyvsphere.vim25 import Vim, ManagedObject

def test(vim, options):
    pass

def clone_vms(vim, options):
    def prepare_clone(vm, clonename, nuke_old=False, datastore=None):
        def done(task):
            return (hasattr(task, 'info') and
                    (task.info.state == 'success' or
                     task.info.state == 'error'))

        def got_ip(task):
            return (hasattr(task, 'summary') and
                    getattr(task.summary.guest, 'ipAddress', None))

        if nuke_old:
            clone = vim.find_vm_by_name(clonename)
            if clone:
                if options.verbose: print "CLONE(%s) POWEROFF STARTING" % clonename
                task = clone.power_off_task()
                while not done(task):
                    task = (yield task)
                if options.verbose: print "CLONE(%s) POWEOFF DONE" % clonename
                if options.verbose: print "CLONE(%s) DELETE STARTING" % clonename
                task = clone.delete_vm_task()
                while not done(task):
                    task = (yield task)
                if options.verbose: print "CLONE(%s) DELETE DONE" % clonename

        if options.verbose: print "CLONE(%s) CLONE STARTING" % clonename
        task = vm.clone_vm_task(clonename, linked_clone=False, datastore=datastore)
        while not done(task):
            task = (yield task)
        if options.verbose: print "CLONE(%s) CLONE DONE" % clonename

        clone = vim.find_vm_by_name(clonename)

        if options.verbose: print "CLONE(%s) POWERON STARTING" % clonename
        task = clone.power_on_task()
        while not done(task):
            task = (yield task)
        if options.verbose: print "CLONE(%s) POWERON DONE" % clonename

        if options.verbose: print "CLONE(%s) WAITING FOR IP" % (clonename)
        task = clone
        while not got_ip(task):
            task = (yield task)
        if options.verbose: print "CLONE(%s) GOT IP: %s" % (clonename, task.summary.guest.ipAddress)

        if options.verbose: print "CLONE(%s) SNAPSHOT STARTING" % clonename
        task = clone.create_snapshot_task('pristine', memory=True)
        while not done(task):
            task = (yield task)
        if options.verbose: print "CLONE(%s) SNAPSHOT DONE" % clonename

    base_vm = vim.find_vm_by_name(options.base_image, ['storage', 'summary'])
    assert base_vm, "could not find base VM by the name %s" % options.base_image
    # Sum the size of disk images scattered over different datastores
    base_vm.size = sum([x.committed for x in base_vm.storage.perDatastoreUsage])
    assert base_vm.size > 0, "base vm size is zero? Very unlikely..."

    # Find all available datastores and optionally filter it
    datastores = vim.find_entities_by_type('Datastore', ['name', 'summary', 'info'])
    # List all available datastores that contain <datastore_filter> as substring
    base_vm.available_datastores = [x for x in datastores if options.datastore_filter in x.name]
    assert len(base_vm.available_datastores) > 0, "datastore filter '%s' did not mach any of the available datastores: %s" % \
        (options.datastore_filter, ','.join([x.name for x in datastores]))

    ops = {}
    tasks = {}

    def place_vm(base_vm, placement_strategy='random'):
        import random
        assert placement_strategy in ['random', 'most-space'], "unknown placement strategy, must be either 'random' or 'most-space'"
        # Make a list of datastores that have enough space and sort it by free space
        possible_targets = sorted([x for x in base_vm.available_datastores if x.summary.freeSpace > base_vm.size], key=lambda x: x.summary.freeSpace, reverse=True)
        assert len(possible_targets) > 0, "no suitable datastore found. Are they all low on space?"
        if placement_strategy == 'random':
            target = random.choice(possible_targets)
        if placement_strategy == 'most-space':
            target = possible_targets[0]
        target.summary.freeSpace -= base_vm.size
        return target

    for i in range(options.count):
        vm_name = "%s-%02d" % (options.vm_name, i)
        datastore = place_vm(base_vm)
        if options.verbose: print "Placing %s to %s" % (vm_name, datastore.name)
        ops[i] = prepare_clone(base_vm, vm_name, True, datastore=datastore)
        tasks[i] = None

    while ops:
        if [tasks[x] for x in tasks if tasks[x]]:
            _,tasks = vim.update_many_objects(tasks)
        for op_key in list(ops):
            try:
                tasks[op_key] = ops[op_key].send(tasks[op_key])
            except StopIteration:
                del tasks[op_key]
                del ops[op_key]
        # if options.verbose: print "Still working,", len(ops), "operations active"
        time.sleep(2)


def delete_vms(vim, options):
    """ Delete a batch of VMs """
    clones = [vim.find_vm_by_name(options.vm_name+"-%02d" % x) for x in range(options.count)]
    for clone in [x for x in clones if x]:
        try:
            if options.verbose: print "POWERING OFF", clone.name
            clone.power_off()
        except:
            pass
        if options.verbose: print "DELETING", clone.name
        clone.delete_vm()


def list_ips(vim, options):
    """ List the IP addresses of a number of VMs """
    clones = [vim.find_vm_by_name(options.vm_name+"-%02d" % x) for x in range(options.count)]

    # Update ell the clones once
    map(lambda x: x.update_local_view(['name', 'summary']), clones)

    waiting_for_ips = True
    while waiting_for_ips:
        if options.verbose: print "-" * 40
        have_it_all = True

        # Update the empty ones
        for clone in [clone for clone in clones if not getattr(clone.summary.guest, 'ipAddress', None)]:
            clone.update_local_view(['name', 'summary'])

        have_it_all = True
        for clone in clones:
            ip_address = getattr(clone.summary.guest, 'ipAddress', None)
            if not ip_address:
                have_it_all = False
            if options.verbose: print clone.name, ip_address if ip_address else "<NO IP ASSIGNED YET>"
        if have_it_all:
            break

def snapshot(vim, options):
    vm = vim.find_vm_by_name(options.vm_name)
    vm.create_snapshot(options.snapshot, memory=True)

def revert(vim, options):
    vm = vim.find_vm_by_name(options.vm_name)
    vm.revert_to_current_snapshot()

def main():
    parser = optparse.OptionParser("Usage: %prog [options]")
    parser.add_option("--debug",
                      action="store_true", dest="debug", default=False,
                      help="Turn on noisy logging")
    parser.add_option("--clone",
                      action="store_true", dest="clone", default=False,
                      help="Clone VMs from a base image")
    parser.add_option("--snapshot",
                      dest="snapshot", default=None,
                      help="Take a snapshot with <name>")
    parser.add_option("--revert",
                      action="store_true", dest="revert", default=False,
                      help="Revert to current snapshot")
    parser.add_option("--delete",
                      action="store_true", dest="delete", default=False,
                      help="Delete VMs")
    parser.add_option("--list-ips",
                      action="store_true", dest="list_ips", default=False,
                      help="List IP addresses of VMs")
    parser.add_option("--test",
                      action="store_true", dest="test", default=False,
                      help="do some testing craziness")
    parser.add_option("--count", dest="count", type="int", default=1,
                      help="Number of VMs to process")
    parser.add_option("--base-image", dest="base_image",
                      help="Name of the image to use as base for cloning")
    parser.add_option("--datastore-filter", dest="datastore_filter", default="",
                      help="place the clones VMs to datastores which contain the filter substring")
    parser.add_option("--vm-name", dest="vm_name",
                      help="Name of VM (used as a prefix in batch operations)")
    parser.add_option("--username", dest="vi_username", default=None,
                      help="vSphere user name")
    parser.add_option("--password", dest="vi_password", default=None,
                      help="vSphere password")
    parser.add_option("--url", dest="vi_url", default=None,
                      help="vSphere URL (https://<your_server>/sdk)")
    parser.add_option("-v", "--verbose",
                      action="store_true", dest="verbose", default=False,
                      help="keeps you well informed when running")
    (options, args) = parser.parse_args()

    vi_url = options.vi_url or os.environ.get('VI_URL')
    assert vi_url, "either the enviroment variable VI_URL or --url needs to be specified"
    vi_username = options.vi_username or os.environ.get('VI_USERNAME')
    assert vi_username, "either the enviroment variable VI_USERNAME or --username needs to be specified"
    vi_password = options.vi_password or os.environ.get('VI_PASSWORD')
    assert vi_password, "either the enviroment variable VI_PASSWORD or --password needs to be specified"

    vim = Vim(vi_url, debug=options.debug)
    if options.verbose: print "CONNECTION complete"
    vim.login(vi_username, vi_password)
    if options.verbose: print "LOGIN complete"

    if options.clone:
        clone_vms(vim, options)

    if options.list_ips:
        list_ips(vim, options)

    if options.delete:
        delete_vms(vim, options)

    if options.snapshot:
        snapshot(vim, options)

    if options.revert:
        revert(vim, options)

    if options.test:
        test(vim, options)

if __name__ == '__main__':
    main()