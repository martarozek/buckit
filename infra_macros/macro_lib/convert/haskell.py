#!/usr/bin/env python2

# Copyright 2016-present, Facebook, Inc.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree. An additional grant
# of patent rights can be found in the PATENTS file in the same directory.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import collections
import os
import re

macro_root = read_config('fbcode', 'macro_lib', '//macro_lib')
include_defs("{}/convert/base.py".format(macro_root), "base")
include_defs("{}/convert/cpp.py".format(macro_root), "cpp")
include_defs("{}/rule.py".format(macro_root))
include_defs("{}/fbcode_target.py".format(macro_root), "target")
load("{}:fbcode_target.py".format(macro_root),
     "RootRuleTarget",
     "RuleTarget",
     "ThirdPartyRuleTarget")
load("@fbcode_macros//build_defs:platform_utils.bzl", "platform_utils")


# Flags controlling warnings issued by compiler
DEFAULT_WARNING_FLAGS = ('-Wall', '-Werror')

# The valid warnings flags
VALID_WARNINGS_FLAGS = frozenset([
    '-W',
    '-w',
    '-Wall',
    '-Werror',
    '-Wwarn',
])
VALID_WARNINGS_FLAGS_RE = re.compile('^(-f(no-)?warn-)|(-W(no-)?)')

VALID_COMPILER_FLAGS_RE = re.compile(
    '^-O[0-9]*$|'
    '^(-D\w+)$|'
    '^(-D\w+=\w+)$|'
    '^(-U\w+)$|'
    '^-rtsopts$|'
    '^-f.*|'
    '^-ddump.*|'
    '^-opt.*|'
    '^-j\d*$|'
    '^-with-rtsopts=.*$|'
    '^-g[0-2]?$|'
    '^-threaded$')

# Regexp matching valid language option flags
LANG_OPT_RE = re.compile(
    '(-X\w+)$|'
    '(-f(no-)?irrefutable-tuples)$|'
    '(-fcontext-stack=\d+)$')

# List of packages:
# - that GHC installs (from third-party2/ghc/<version>/<platform>/TARGETS)
# - except those that are superseded by newer versions in stackage
INTERNAL_GHC_PACKAGES = [
    'array',
    'base',
    'binary',
    'bytestring',
    'Cabal',
    'compact',
    'containers',
    'deepseq',
    'directory',
    'filepath',
    'ghc',
    'ghci',
    'ghc-boot',
    'ghc-boot-th',
    'ghc-prim',
    'hoopl',
    'hpc',
    'integer-gmp',
    'pretty',
    'process',
    'rts',
    'template-haskell',
    'terminfo',
    'time',
    'transformers',
    'unix',
]

# Extensions enabled by default unless you specify fb_haskell = False
FB_HASKELL_LANG = [
    'BangPatterns',
    'BinaryLiterals',
    'DataKinds',
    'DeriveDataTypeable',
    'DeriveGeneric',
    'EmptyCase',
    'ExistentialQuantification',
    'FlexibleContexts',
    'FlexibleInstances',
    'GADTs',
    'GeneralizedNewtypeDeriving',
    'LambdaCase',
    'MultiParamTypeClasses',
    'MultiWayIf',
    'NoMonomorphismRestriction',
    'OverloadedStrings',
    'PatternSynonyms',
    'RankNTypes',
    'RecordWildCards',
    'ScopedTypeVariables',
    'StandaloneDeriving',
    'TupleSections',
    'TypeFamilies',
    'TypeSynonymInstances',
]

FB_HASKELL_LANG_OPTS = map(lambda x: '-X' + x, FB_HASKELL_LANG)

# Packages enabled by default unless you specify fb_haskell = False
FB_HASKELL_PACKAGES = [
    'aeson',
    'async',
    'attoparsec',
    'binary',
    'bytestring',
    'containers',
    'data-default',
    'deepseq',
    'directory',
    'either',
    'filepath',
    'hashable',
    'mtl',
    'optparse-applicative',
    'pretty',
    'process',
    'scientific',
    'statistics',
    'text',
    'text-show',
    'time',
    'transformers',
    'unordered-containers',
    'QuickCheck',
    'unix',
    'vector',
]

FB_HASKELL_COMPILER_FLAGS = [
    '-rtsopts',
]

IMPLICIT_TP_DEPS = [
    ThirdPartyRuleTarget('ghc', 'base'),

    # TODO(agallagher): These probably need to be moved into the TARGETS
    # rule definition for a core lib.
    ThirdPartyRuleTarget('glibc', 'dl'),
    ThirdPartyRuleTarget('glibc', 'm'),
    ThirdPartyRuleTarget('glibc', 'pthread'),
]

ALEX = ThirdPartyRuleTarget('hs-alex', 'alex')
ALEX_PACKAGES = ['array', 'bytestring']

HAPPY = ThirdPartyRuleTarget('hs-happy', 'happy')
HAPPY_PACKAGES = ['array']

HSC2HS_TEMPL = '''\
set -e
mkdir -p `dirname "$OUT"`

# The C/C++ toolchain currently expects we're running from the root of fbcode.
cd {fbcode}

# The `hsc2hs` tool.
args=({ghc_tool}/bin/hsc2hs)

# Keep hsc2hs's internal files around, since this is useful for debugging and
# doesn't hurt us.
args+=("--keep-files")

args+=("--template=template-hsc.h")

# Always define __HSC2HS__ in the C program and the compiled Haskell file, and
# the C header.
args+=("--define=__HSC2HS__")

# We need to pass "-x c++" to the compiler that hsc2hs invokes, but *before*
# any file paths; hsc2hs passes flags *after* paths. The easy and morally
# tolerable workaround is to generate a shell script that partially applies
# the flag.
CC_WRAP="$OUT".cc_wrap.sh
echo >  "$CC_WRAP" '#!/bin/sh'

# TODO: T23700463 Turn distcc back on
echo >> "$CC_WRAP" 'BUCK_DISTCC=0 $(cxx) -x c++ "$@"'
chmod +x "$CC_WRAP"
# Set 'CXX' locally to the real compiler being invoked, so that hsc2hs plugins
# needing to invoke the compiler can do so correctly.
export CXX="$CC_WRAP"
args+=("--cc=$CC_WRAP")

# Pass in the C/C++ compiler and preprocessor flags.
cflags=()
cflags+=("-fpermissive")
cflags+=($(cxxflags))
cflags+=($(cxxppflags{deps}))
ltoflag=""
# Needed for `template-hsc.h`.
cflags+=(-I{ghc}/lib/ghc-8.0.2)
for cflag in "${{cflags[@]}}"; do
  if [[ "$cflag" == "-flto" || "$cflag" =~ "-flto=" ]]; then
    ltoflag="$cflag"
  fi
  args+=(--cflag="$cflag")
done

# Add in the C/C++ linker.
args+=("--ld=$(ld)")

# Add in the linker flags.
ldflags=($(ldflags-{link_style}{deps}))
if [ ! -z "$ltoflag" ]; then
    ldflags+=("$ltoflag")
fi
ldflags+=("-o" "`dirname $OUT`/{out_obj}")
for ldflag in "${{ldflags[@]}}"; do
  args+=(--lflag="$ldflag")
done

# Link the "run once" hsc2hs binary stripped. This makes some hsc files
# go from 20s to 10s and the "run once" binary from 800M to 40M when
# statically linked. Situations where one would want to debug them are
# very rare.
# This doesn't make a difference when dynamically linked.
args+=("--lflag=-Xlinker")
args+=("--lflag=-s")

# When linking in `dev` mode, make sure that the ASAN symbols that get linked
# into the top-level binary are made available for any dependent libraries.
if [ "{link_style}" == "shared" ]; then
  args+=("--lflag=-Xlinker")
  args+=("--lflag=--export-dynamic")
fi;

# The output file and input source.
args+=("-o" "$OUT")
args+=("$SRCS")

exec "${{args[@]}}"
'''

C2HS_TEMPL = '''\
set -e
mkdir -p `dirname "$OUT"`

# The C/C++ toolchain currently expects we're running from the root of fbcode.
cd {fbcode}

# The `c2hs` tool.
args=({stackage}/bin/c2hs)

# Add in the C/C++ preprocessor.
args+=("--cpp="$(cc))

# Add in C/C++ preprocessor flags.
cppflags=(-E)
cppflags+=($(cppflags{deps}))
for cppflag in "${{cppflags[@]}}"; do
  args+=("--cppopts=$cppflag")
done

# The output file and input source.
args+=("-o" "$OUT")
args+=("$SRCS")

exec "${{args[@]}}"
'''


class HaskellConverter(base.Converter):

    def __init__(self, context, rule_type, buck_rule_type=None):
        super(HaskellConverter, self).__init__(context)
        self._rule_type = rule_type
        self._buck_rule_type = buck_rule_type or rule_type

    def get_fbconfig_rule_type(self):
        return self._rule_type

    def get_buck_rule_type(self):
        return self._buck_rule_type

    def is_binary(self):
        return self.get_fbconfig_rule_type() in (
            'haskell_binary',
            'haskell_unittest')

    def is_deployable(self):
        return self.get_fbconfig_rule_type() in (
            'haskell_binary',
            'haskell_unittest',
            'haskell_ghci')

    def is_test(self):
        return self.get_fbconfig_rule_type() in ('haskell_unittest',)

    def get_dep_for_package(self, package):
        """
        Convert arguments in the `package` parameter to actual deps.
        """

        if isinstance(package, basestring):
            version = None
        else:
            package, version = package

        if package in INTERNAL_GHC_PACKAGES:
            project = 'ghc'
        else:
            project = 'stackage-lts'

        return ThirdPartyRuleTarget(project, package)

    def get_deps_for_packages(self, packages):
        return [self.get_dep_for_package(p) for p in packages]

    def get_implicit_deps(self):
        """
        The deps that all haskell rules implicitly depend on.
        """

        return IMPLICIT_TP_DEPS

    def get_warnings_flags(self, warnings_flags=None):
        """
        Return the flags responsible for controlling the warnings used in
        compilation.
        """

        # Set the warnings flags for this rule by appending the user supplied
        # warnings flags to the default ones.
        wflags = []
        wflags.extend(DEFAULT_WARNING_FLAGS)
        if warnings_flags is not None:
            wflags.extend(warnings_flags)

        # Verify that all the warning flags are valid
        bad_warnings_flags = set()
        for flag in wflags:
            if flag in VALID_WARNINGS_FLAGS:
                continue
            if VALID_WARNINGS_FLAGS_RE.search(flag):
                continue
            bad_warnings_flags.add(flag)
        if bad_warnings_flags:
            raise ValueError(
                'invalid warnings flags: {!r}'
                .format(sorted(bad_warnings_flags)))

        return tuple(wflags)

    def get_compiler_flags(self, user_compiler_flags, fb_haskell):
        """
        Get flags to use that affect compilation.
        """

        compiler_flags = []

        # Verify that all the user provided compiler flags are valid.
        bad_compiler_flags = set()
        rts_flags = False
        for flag in user_compiler_flags:
            if rts_flags:
                if flag == '-RTS':
                    rts_flags = False
            elif flag == '+RTS':
                rts_flags = True
            elif VALID_COMPILER_FLAGS_RE.match(flag) is None:
                bad_compiler_flags.add(flag)
        if bad_compiler_flags:
            raise ValueError(
                'invalid compiler flags: {!r}'
                .format(sorted(bad_compiler_flags)))
        compiler_flags.extend(user_compiler_flags)

        if fb_haskell:
            compiler_flags.extend(FB_HASKELL_COMPILER_FLAGS)

        if self.get_sanitizer() == 'address':
            compiler_flags.append('-optP-D__SANITIZE_ADDRESS__')

        return tuple(compiler_flags)

    def get_language_options(self, options, fb_haskell):
        """
        Get the language options from user provided options.
        """

        bad_opts = []
        for opt in options:
            if not LANG_OPT_RE.match(opt):
                bad_opts.append(opt)
        if bad_opts:
            raise ValueError('invalid language options: {!r}'.format(bad_opts))

        if fb_haskell:
            return sorted(set(options) | set(FB_HASKELL_LANG_OPTS))
        else:
            return options

    def convert_happy(self, name, platform, happy_src, visibility):
        """
        Create rules to generate a Haskell source from the given happy file.
        """

        rules = []

        attrs = collections.OrderedDict()
        attrs['name'] = name + '-' + happy_src
        if visibility is not None:
            attrs['visibility'] = visibility
        attrs['out'] = os.path.splitext(happy_src)[0] + '.hs'
        attrs['srcs'] = [happy_src]
        attrs['cmd'] = ' && '.join([
            'mkdir -p `dirname "$OUT"`',
            '$(exe {happy}) -o "$OUT" -ag "$SRCS"'.format(
                happy=self.get_tool_target(HAPPY, platform)),
        ])
        rules.append(Rule('genrule', attrs))

        return (':' + attrs['name'], rules)

    def convert_alex(self, name, platform, alex_src, visibility):
        """
        Create rules to generate a Haskell source from the given alex file.
        """

        rules = []

        attrs = collections.OrderedDict()
        attrs['name'] = name + '-' + alex_src
        if visibility is not None:
            attrs['visibility'] = visibility
        attrs['out'] = os.path.splitext(alex_src)[0] + '.hs'
        attrs['srcs'] = [alex_src]
        attrs['cmd'] = ' && '.join([
            'mkdir -p `dirname "$OUT"`',
            '$(exe {alex}) -o "$OUT" -g "$SRCS"'.format(
                alex=self.get_tool_target(ALEX, platform)),
        ])
        rules.append(Rule('genrule', attrs))

        return (':' + attrs['name'], rules)

    def _get_dep_rule(self, base_path, name, deps, visibility):
        """
        Sets up a dummy rule with the given dep objects formatted and installed
        using `deps` and `platform_deps` to support multi-platform builds.

        This is useful to package a given dep list, which requires multi-
        platform dep parameter support, into a single target that can be used
        in interfaces that don't have this support (e.g. macros in `genrule`s
        and `cxx_genrule`).
        """

        attrs = collections.OrderedDict()
        attrs['name'] = name
        if visibility is not None:
            attrs['visibility'] = visibility
        attrs['preferred_linkage'] = 'static'
        attrs['deps'], attrs['platform_deps'] = self.format_all_deps(deps)
        # Setup platform default for compilation DB, and direct building.
        buck_platform = platform_utils.get_buck_platform_for_base_path(base_path)
        attrs['default_platform'] = buck_platform
        attrs['defaults'] = {'platform': buck_platform}
        return Rule('cxx_library', attrs)

    def convert_c2hs(self, base_path, name, platform, source, deps, visibility):
        """
        Construct the rules to generate a haskell source from the given `c2hs`
        source.
        """

        rules = []

        # Macros in the `cxx_genrule` below don't support the `platform_deps`
        # parameter that we rely on to support multi-platform builds.  So use
        # a helper rule for this, and just depend on the helper.
        deps_name = name + '-' + source + '-deps'
        d, r = self.get_binary_link_deps(base_path, deps_name)
        rules.append(self._get_dep_rule(base_path, deps_name, deps + d, visibility))
        rules.extend(r)

        attrs = collections.OrderedDict()
        attrs['name'] = name + '-' + source
        if visibility is not None:
            attrs['visibility'] = visibility
        attrs['cmd'] = (
            C2HS_TEMPL.format(
                fbcode=(
                    os.path.join(
                        '$GEN_DIR',
                        self.get_fbcode_dir_from_gen_dir())),
                stackage=self.get_tp2_dep_path('stackage-lts', platform),
                deps=' :' + deps_name))
        attrs['srcs'] = [source]
        attrs['out'] = os.path.splitext(source)[0] + '.hs'
        rules.append(Rule('cxx_genrule', attrs))

        return (':' + attrs['name'], rules)

    def convert_hsc2hs(
            self,
            base_path,
            name,
            platform,
            source,
            deps,
            visibility):
        """
        Construct the rules to generate a haskell source from the given
        `hsc2hs` source.
        """

        rules = []

        # Macros in the `cxx_genrule` below don't support the `platform_deps`
        # parameter that we rely on to support multi-platform builds.  So use
        # a helper rule for this, and just depend on the helper.
        deps_name = name + '-' + source + '-deps'
        d, r = self.get_binary_link_deps(base_path, deps_name)
        rules.append(self._get_dep_rule(base_path, deps_name, deps + d, visibility))
        rules.extend(r)

        out_obj = os.path.splitext(os.path.basename(source))[0] + "_hsc_make"

        attrs = collections.OrderedDict()
        attrs['name'] = name + '-' + source
        if visibility is not None:
            attrs['visibility'] = visibility
        attrs['cmd'] = (
            HSC2HS_TEMPL.format(
                fbcode=(
                    os.path.join(
                        '$GEN_DIR',
                        self.get_fbcode_dir_from_gen_dir())),
                ghc_tool=self.get_tp2_tool_path('ghc', platform),
                ghc=self.get_tp2_dep_path('ghc', platform),
                link_style=self._context.link_style,
                deps=' :' + deps_name,
                out_obj=out_obj))
        attrs['srcs'] = [source]
        attrs['out'] = os.path.splitext(source)[0] + '.hs'
        rules.append(Rule('cxx_genrule', attrs))

        return (':' + attrs['name'], rules)

    def convert_rule(
            self,
            base_path,
            name=None,
            main=None,
            srcs=(),
            deps=(),
            external_deps=(),
            packages=(),
            compiler_flags=(),
            warnings_flags=(),
            lang_opts=(),
            enable_haddock=False,
            haddock_flags=None,
            enable_profiling=None,
            ghci_bin_dep=None,
            ghci_init=None,
            extra_script_templates=(),
            eventlog=None,
            link_whole=None,
            force_static=None,
            fb_haskell=True,
            allocator='jemalloc',
            dlls={},
            visibility=None):

        rules = []
        out_compiler_flags = []
        out_linker_flags = []
        out_link_style = self.get_link_style()
        platform = self.get_platform(base_path)

        attributes = collections.OrderedDict()
        attributes['name'] = name
        if visibility is not None:
            attributes['visibility'] = visibility

        if self.is_binary():
            if main is not None:
                attributes['main'] = main
            elif not ("Main.hs" in srcs):
                raise ValueError(
                    'Must define `main` attribute on {0}:{1}'.format(
                        base_path, name))

        if link_whole is not None:
            attributes['link_whole'] = link_whole

        if force_static is not None:
            attributes['preferred_linkage'] = 'static'

        if self.get_fbconfig_rule_type() == 'haskell_ghci':
            out_compiler_flags.append('-fexternal-interpreter')
            # Mark binary_link_deps to be preloaded
            d, r = self.get_binary_link_deps(base_path, name, allocator=allocator)
            attributes['preload_deps'], attributes['platform_preload_deps'] = \
                self.format_all_deps(d)

            attributes['extra_script_templates'] = map(
                lambda template : self.convert_source(base_path, template),
                extra_script_templates)
            template_base_names = []
            # BUCK generates a standard script with the same name as TARGET
            # by default
            template_base_names.append(name)
            for templatePath in attributes['extra_script_templates']:
                template_base_names.append(os.path.basename(templatePath))
            if len(template_base_names) > len(set(template_base_names)):
                raise ValueError(
                    '{0}:{1}: parameter `extra_script_templates`: '.format(
                        base_path, name) +
                    'Template file names must be unique and not same as ' +
                    'the TARGET name')
            rules.extend(r)

        if ghci_bin_dep is not None:
            bin_dep_target = self.convert_build_target(base_path, ghci_bin_dep)
            attributes['ghci_bin_dep'] = bin_dep_target

        if ghci_init is not None:
            attributes['ghci_init'] = self.convert_source(base_path, ghci_init)

        if self.read_hs_profile():
            attributes['enable_profiling'] = True
        elif enable_profiling is not None:
            attributes['enable_profiling'] = enable_profiling

        if self.read_hs_eventlog():
            out_linker_flags.append('-eventlog')
        if self.read_hs_debug():
            out_linker_flags.append('-debug')

        if self.get_fbconfig_rule_type() == 'haskell_library':
            out_haddock_flags = [
                 '--source-entity',
                 'https://phabricator.intern.facebook.com/diffusion/FBS/browse/' +
                 'master/fbcode/%{FILE}$%{LINE}',
            ]
            # keep TARGETS specific flags last, so that they can override the
            # flags before
            if haddock_flags:
                out_haddock_flags.extend(haddock_flags)
            attributes['haddock_flags'] = out_haddock_flags

        validated_compiler_flags = []
        validated_compiler_flags.extend(
            self.get_compiler_flags(compiler_flags, fb_haskell))
        ldflags = (
            self.get_ldflags(
                base_path,
                name,
                self.get_fbconfig_rule_type(),
                binary=self.is_binary(),
                deployable=self.is_deployable(),
                # Never apply stripping flags to library rules, as they only
                # get linked when using dynamic linking (which we avoid
                # applying stripping to anyway), and added unused linker flags
                # affect rule keys up the tree.
                strip_mode=None if self.is_deployable() else 'none',
                build_info=self.is_deployable(),
                platform=platform if self.is_deployable() else None))
        for ldflag in ldflags:
            out_linker_flags.extend(['-optl', ldflag])
        out_linker_flags.extend(validated_compiler_flags)

        out_compiler_flags.extend(self.get_warnings_flags(warnings_flags))
        out_compiler_flags.extend(validated_compiler_flags)
        out_compiler_flags.extend(
            self.get_language_options(lang_opts,fb_haskell))
        build_mode = self.get_build_mode()
        if build_mode is not None:
            out_compiler_flags.extend(build_mode.ghc_flags)
        out_compiler_flags.extend(self.read_extra_ghc_compiler_flags())
        if out_compiler_flags:
            attributes['compiler_flags'] = out_compiler_flags

        # If this is binary and we're using the shared link style, set this in
        # the output attributes.
        if self.is_deployable() and self._context.link_style == 'shared':
            out_link_style = 'shared'

        # Collect all deps specified by the user.
        user_deps = []
        for dep in deps:
            user_deps.append(target.parse_target(dep, base_path=base_path))
        for dep in external_deps:
            user_deps.append(self.normalize_external_dep(dep))
        user_deps.extend(self.get_deps_for_packages(packages))
        if fb_haskell:
            user_deps.extend(self.get_deps_for_packages(
                x for x in FB_HASKELL_PACKAGES if x not in packages))
        user_deps.extend(self.get_implicit_deps())

        # Convert the various input source types to haskell sources.
        out_srcs = []
        implicit_src_deps = set()
        for src in srcs:
            _, ext = os.path.splitext(src)
            if ext == '.y':
                src, extra_rules = self.convert_happy(name, platform, src, visibility)
                out_srcs.append(src)
                rules.extend(extra_rules)
                implicit_src_deps.update(
                    self.get_deps_for_packages(HAPPY_PACKAGES))
            elif ext == '.x':
                src, extra_rules = self.convert_alex(name, platform, src, visibility)
                out_srcs.append(src)
                rules.extend(extra_rules)
                implicit_src_deps.update(
                    self.get_deps_for_packages(ALEX_PACKAGES))
            elif ext == '.hsc':
                src, extra_rules = (
                    self.convert_hsc2hs(
                        base_path,
                        name,
                        platform,
                        src,
                        user_deps,
                        visibility))
                out_srcs.append(src)
                rules.extend(extra_rules)
            elif ext == '.chs':
                src, extra_rules = (
                    self.convert_c2hs(
                        base_path,
                        name,
                        platform,
                        src,
                        user_deps,
                        visibility))
                out_srcs.append(src)
                rules.extend(extra_rules)
            else:
                out_srcs.append(src)
        attributes['srcs'] = out_srcs

        # The final list of dependencies.
        dependencies = []
        dependencies.extend(user_deps)
        dependencies.extend(x for x in sorted(implicit_src_deps)
                            if x not in user_deps)

        # Handle DLL deps.
        out_dep_queries = []
        if dlls:
            buck_platform = platform_utils.get_buck_platform_for_base_path(base_path)
            dll_rules, dll_deps, dll_ldflags, dll_dep_queries = (
                cpp.convert_dlls(base_path, name, platform, buck_platform,
                                 dlls, self.get_fbcode_dir_from_gen_dir(),
                                 visibility=visibility))
            rules.extend(dll_rules)
            dependencies.extend(dll_deps)
            optlflags = []
            for f in dll_ldflags:
                optlflags.append("-optl")
                optlflags.append(f)
            out_linker_flags.extend(optlflags)
            out_dep_queries.extend(dll_dep_queries)

            # We don't currently support dynamic linking with DLL support, as
            # we don't have a great way to prevent dependency DSOs needed by
            # the DLL, but *not* needed by the top-level binary, from being
            # dropped from the `DT_NEEDED` tags when linking with
            # `--as-needed`.
            if out_link_style == 'shared':
                out_link_style = 'static_pic'

        if out_dep_queries:
            attributes['deps_query'] = ' union '.join(out_dep_queries)
            attributes['link_deps_query_whole'] = True

        out_linker_flags.extend(self.read_extra_ghc_linker_flags())
        if out_linker_flags:
            attributes['linker_flags'] = out_linker_flags

        if self.is_deployable():
            attributes['platform'] = platform_utils.get_buck_platform_for_base_path(base_path)

            # TODO: support `link_style` for `haskell_ghci` rule.
            if self.get_fbconfig_rule_type() != 'haskell_ghci':
                attributes['link_style'] = out_link_style

        # Add in binary-specific link deps.
        if self.is_binary():
            d, r = self.get_binary_link_deps(base_path, name, allocator=allocator)
            dependencies.extend(d)
            rules.extend(r)
        if self.is_test():
            dependencies.append(self.get_dep_for_package('HUnit'))
            dependencies.append(RootRuleTarget('tools/test/stubs', 'fbhsunit'))


        if self.get_fbconfig_rule_type() in ['haskell_library', 'haskell_binary']:
            # Mark binary_link_deps to be preloaded
            d, r = self.get_binary_link_deps(base_path, name, allocator=allocator)
            attributes['ghci_preload_deps'], attributes['ghci_platform_preload_deps'] = \
                self.format_all_deps(d)
            if self.get_fbconfig_rule_type() == 'haskell_library':
                rules.extend(r)

        attributes['deps'], attributes['platform_deps'] = (
            self.format_all_deps(dependencies))

        rules.append(Rule(self.get_buck_rule_type(), attributes))

        return rules

    def convert_unittest(
            self,
            base_path,
            name,
            tags=(),
            env=None,
            visibility=None,
            **kwargs):
        """
        Buckify a unittest rule.
        """

        rules = []

        # Generate the test binary rule and fixup the name.
        binary_name = name + '-binary'
        binary_rules = (
            self.convert_rule(
                base_path,
                name=binary_name,
                visibility=visibility,
                **kwargs))
        rules.extend(binary_rules)

        # Create a `sh_test` rule to wrap the test binary and set it's tags so
        # that testpilot knows it's a haskell test.
        attributes = collections.OrderedDict()
        attributes['name'] = name
        if visibility is not None:
            attributes['visibility'] = visibility
        attributes['test'] = ':' + binary_name
        attributes['env'] = env
        platform = self.get_platform(base_path)
        attributes['labels'] = (
            self.convert_labels(platform, 'haskell', 'custom-type-hs', *tags))
        rules.append(Rule('sh_test', attributes))

        return rules

    def convert_dll(self, base_path, name, dll, visibility=None, **kwargs):
        """
        Generate rules to build a dynamic library.
        """

        rules = []

        # Generate rules to build the haskell library.  We set `link_whole`
        # here as it'll be the main component of the shared library we build
        # below.  We also use an obsfucated name so that dependents must use
        # their `dll` parameter to depend on it.
        lib_name = name + '-dll-root'
        rules.extend(
            self.convert_rule(
                base_path,
                lib_name,
                link_whole=True,
                force_static=True,
                visibility=visibility,
                **kwargs))

        # For backwards compatiblity with fbbuild, generate a noop rule under
        # the original name.  This is so unported fbbuild use cases of DLLs
        # don't break the build.
        attrs = collections.OrderedDict()
        attrs['name'] = name
        if visibility is not None:
            attrs['visibility'] = visibility
        attrs['out'] = 'empty'
        attrs['cmd'] = 'touch "$OUT"'
        rules.append(Rule('genrule', attrs))

        return rules

    def convert_library(self, base_path, name, dll=None, **kwargs):
        """
        Generate rules for a haskell library.
        """

        if dll is None:
            return self.convert_rule(base_path, name, **kwargs)
        else:
            return self.convert_dll(base_path, name, dll, **kwargs)

    def convert_haddock(
            self,
            base_path,
            name,
            deps=(),
            haddock_flags=(),
            visibility=None):
        rules = []

        attrs = collections.OrderedDict()
        attrs['name'] = name
        if visibility is not None:
            attrs['visibility'] = visibility
        if haddock_flags:
            attrs['haddock_flags'] = haddock_flags
        attrs['platform'] = platform_utils.get_buck_platform_for_base_path(base_path)

        out_deps = []
        for target in deps:
            out_deps.append(self.convert_build_target(base_path, target))
        attrs['deps'] = out_deps

        rules.append(Rule('haskell_haddock', attrs))

        return rules

    def convert(self, *args, **kwargs):
        """
        Generate rules for a haskell rule.
        """

        rtype = self.get_fbconfig_rule_type()
        if rtype == 'haskell_binary':
            return self.convert_rule(*args, **kwargs)
        if rtype == 'haskell_library':
            return self.convert_library(*args, **kwargs)
        elif rtype == 'haskell_unittest':
            return self.convert_unittest(*args, **kwargs)
        elif rtype == 'haskell_ghci':
            return self.convert_rule(*args, **kwargs)
        elif rtype == 'haskell_haddock':
            return self.convert_haddock(*args, **kwargs)
        else:
            raise Exception('unexpected type: ' + rtype)
