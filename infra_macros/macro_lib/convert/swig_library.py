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
import pipes

with allow_unsafe_import():  # noqa: magic
    import os


# Hack to make internal Buck macros flake8-clean until we switch to buildozer.
def import_macro_lib(path):
    global _import_macro_lib__imported
    include_defs('{}/{}.py'.format(  # noqa: F821
        read_config('fbcode', 'macro_lib', '//macro_lib'), path  # noqa: F821
    ), '_import_macro_lib__imported')
    ret = _import_macro_lib__imported
    del _import_macro_lib__imported  # Keep the global namespace clean
    return ret


base = import_macro_lib('convert/base')
cpp = import_macro_lib('convert/cpp')
Rule = import_macro_lib('rule').Rule
target = import_macro_lib('fbcode_target')
RootRuleTarget = target.RootRuleTarget
RuleTarget = target.RuleTarget
ThirdPartyRuleTarget = target.ThirdPartyRuleTarget
load("@fbcode_macros//build_defs:python_typing.bzl",
     "get_typing_config_target")


FLAGS = [
    '-c++',
    '-Werror',
    '-Wextra',
]


class SwigLangConverter(base.Converter):
    """
    Base class for language-specific converters.  New languages should
    subclass this class.
    """

    def get_lang(self):
        """
        Return the language name.
        """

        raise NotImplementedError()

    def get_lang_opt(self):
        """
        Return the language flag to pass into swig.
        """

        raise NotImplementedError()

    def get_lang_flags(self, **kwargs):
        """
        Return language specific flags to pass to swig.
        """

        return []

    def get_generated_sources(self, module):
        """
        Return the language-specific sources generated by swig.
        """

        raise NotImplementedError()

    def get_language_rule(
            self,
            base_path,
            name,
            module,
            hdr,
            src,
            gen_srcs,
            cpp_deps,
            deps,
            visibility,
            **kwargs):
        """
        Generate the language-specific library rule (and any extra necessary
        rules).
        """

        raise NotImplementedError()


class JavaSwigConverter(SwigLangConverter):
    """
    Specializer to support generating Java libraries from swig sources.
    """

    def get_lang(self):
        return 'java'

    def get_lang_opt(self):
        return '-java'

    def get_lang_flags(self, java_package=None, **kwargs):
        flags = []

        # Forward the user-provided `java_package` parameter.
        if java_package is not None:
            flags.append('-package')
            flags.append(java_package)

        return flags

    def get_generated_sources(self, module):
        return collections.OrderedDict([('', '.')])

    def get_language_rule(
            self,
            base_path,
            name,
            module,
            hdr,
            src,
            gen_srcs,
            cpp_deps,
            deps,
            java_library_name=None,
            visibility=None,
            **kwargs):

        rules = []

        # Build the C/C++ Java extension from the generated C/C++ sources.
        attrs = collections.OrderedDict()
        attrs['name'] = name + '-ext'
        if visibility is not None:
            attrs['visibility'] = visibility
        attrs['srcs'] = [src]
        # Swig-generated code breaks strict-aliasing with gcc
        # (http://www.swig.org/Doc3.0/SWIGDocumentation.html#Java_compiling_dynamic).
        attrs['compiler_flags'] = ['-fno-strict-aliasing']
        attrs['soname'] = (
            'lib{}.so'.format(
                module if java_library_name is None else java_library_name))
        attrs['link_style'] = kwargs.get('java_link_style')
        attrs['deps'], attrs['platform_deps'] = (
            self.format_all_deps(
                cpp_deps + [RootRuleTarget('common/java/jvm', 'jvm')]))
        # When using e.g. %feature("director") in Something.i, SWIG includes
        # "Something.h" in the source code of the C/C++ Java extension.
        attrs['headers'] = [hdr]
        attrs['header_namespace'] = ''
        rules.append(Rule('cxx_library', attrs))

        # Pack all generated source directories into a source zip, which we'll
        # feed into the Java library rule.
        src_zip_name = name + '.src.zip'
        attrs = collections.OrderedDict()
        attrs['name'] = src_zip_name
        if visibility is not None:
            attrs['visibility'] = visibility
        # Java rules are C/C++ platform agnostic, so we're forced to choose a
        # fixed platform at parse-time (which means Java binaries will only
        # ever build against one platform at a time).
        attrs['srcs'] = (
            ['{}#{}'.format(s, self.get_default_platform())
             for s in gen_srcs.values()])
        attrs['out'] = src_zip_name
        rules.append(Rule('zip_file', attrs))

        # Generate the wrapping Java library.
        attrs = collections.OrderedDict()
        attrs['name'] = name
        if visibility is not None:
            attrs['visibility'] = visibility
        attrs['srcs'] = [':' + src_zip_name]
        out_deps = []
        out_deps.extend(deps)
        out_deps.append(':' + name + '-ext')
        attrs['deps'] = out_deps
        rules.append(Rule('java_library', attrs))

        return rules


class PythonSwigConverter(SwigLangConverter):
    """
    Specializer to support generating Python libraries from swig sources.
    """

    def __init__(self, context, *args, **kwargs):
        super(PythonSwigConverter, self).__init__(context, *args, **kwargs)
        self._cpp_python_extension_converter = (
            cpp.CppConverter(context, 'cpp_python_extension'))

    def get_lang(self):
        return 'py'

    def get_lang_opt(self):
        return '-python'

    def get_lang_flags(self, java_package=None, **kwargs):
        return [
            '-threads',
            '-safecstrings',
            '-classic',
        ]

    def get_generated_sources(self, module):
        src = module + '.py'
        return collections.OrderedDict([(src, src)])

    def get_language_rule(
            self,
            base_path,
            name,
            module,
            hdr,
            src,
            gen_srcs,
            cpp_deps,
            deps,
            py_base_module=None,
            visibility=None,
            **kwargs):

        # Build the C/C++ python extension from the generated C/C++ sources.
        out_compiler_flags = []
        # Generated code uses a lot of shadowing, so disable GCC warnings
        # related to this.
        if self._context.compiler == 'gcc':
            out_compiler_flags.append('-Wno-shadow')
            out_compiler_flags.append('-Wno-shadow-local')
            out_compiler_flags.append('-Wno-shadow-compatible-local')
        for rule in self._cpp_python_extension_converter.convert(
            base_path,
            name=name + '-ext',
            srcs=[src],
            base_module=py_base_module,
            module_name='_' + module,
            compiler_flags=out_compiler_flags,
            # This is pretty gross.  We format the deps just to get
            # re-parsed by the C/C++ converter.  Long-term, it'd be
            # be nice to support a better API in the converters to
            # handle higher-leverl objects, but for now we're stuck
            # doing this to re-use other converters.
            deps=self.format_deps([d for d in cpp_deps if d.repo is None]),
            external_deps=[
                (d.repo, d.base_path, None, d.name)
                for d in cpp_deps if d.repo is not None
            ],
        ):
            yield rule
        # Generate the wrapping python library.
        attrs = collections.OrderedDict()
        attrs['name'] = name
        if visibility is not None:
            attrs['visibility'] = visibility
        attrs['srcs'] = gen_srcs
        out_deps = []
        out_deps.extend(deps)
        out_deps.append(':' + name + '-ext')
        attrs['deps'] = out_deps
        if py_base_module is not None:
            attrs['base_module'] = py_base_module
        # At some point swig targets should also include typing Options
        # For now we just need an empty directory.
        if get_typing_config_target():
            yield self.gen_typing_config(name)
        yield Rule('python_library', attrs)


class SwigLibraryConverter(base.Converter):

    def __init__(self, *args, **kwargs):
        super(SwigLibraryConverter, self).__init__(*args, **kwargs)

        # Setup the macro converters.
        converters = [
            JavaSwigConverter(*args, **kwargs),
            PythonSwigConverter(*args, **kwargs),
        ]
        self._converters = {c.get_lang(): c for c in converters}

    def get_fbconfig_rule_type(self):
        return 'swig_library'

    def get_languages(self, langs):
        """
        Convert the `languages` parameter to a normalized list of languages.
        """

        languages = set()

        if langs is None:
            raise TypeError('swig_library() requires `languages` argument')

        if not langs:
            raise TypeError('swig_library() requires at least on language')

        for lang in langs:
            if lang not in self._converters:
                raise TypeError(
                    'swig_library() does not support language {!r}'
                    .format(lang))
            if lang in languages:
                raise TypeError(
                    'swig_library() given duplicate language {!r}'
                    .format(lang))
            languages.add(lang)

        return languages

    def get_exported_include_tree(self, dep):
        """
        Generate the exported swig source includes target use for the given
        swig library target.
        """

        return dep + '-swig-includes'

    def generate_compile_rule(
            self,
            base_path,
            name,
            swig_flags,
            lang,
            interface,
            cpp_deps,
            visibility,
            **kwargs):
        """
        Generate a rule which runs the swig compiler for the given inputs.
        """

        rules = []

        platform = self.get_platform(base_path)
        converter = self._converters[lang]
        base, _ = os.path.splitext(self.get_source_name(interface))
        hdr = base + '.h'
        src = base + '.cc'

        flags = []
        flags.extend(FLAGS)
        flags.extend(swig_flags)
        flags.extend(converter.get_lang_flags(**kwargs))

        gen_name = '{}-{}-gen'.format(name, lang)
        attrs = collections.OrderedDict()
        attrs['name'] = gen_name
        if visibility is not None:
            attrs['visibility'] = visibility
        attrs['out'] = os.curdir
        attrs['srcs'] = [interface]
        cmds = [
            'mkdir -p'
            ' "$OUT"/lang'
            ' \\$(dirname "$OUT"/gen/{src})'
            ' \\$(dirname "$OUT"/gen/{hdr})',
            'export PPFLAGS=(`'
            ' $(exe //tools/build/buck:swig_pp_filter)'
            ' $(cxxppflags{deps})`)',
            'touch "$OUT"/gen/{hdr}',
            '$(exe {swig}) {flags} {lang}'
            ' -I- -I$(location {includes})'
            ' "${{PPFLAGS[@]}}"'
            ' -outdir "$OUT"/lang -o "$OUT"/gen/{src} -oh "$OUT"/gen/{hdr}'
            ' "$SRCS"',
        ]
        attrs['cmd'] = (
            ' && '.join(cmds).format(
                swig=self.get_tool_target(
                    ThirdPartyRuleTarget('swig', 'bin/swig'),
                    platform),
                flags=' '.join(map(pipes.quote, flags)),
                lang=pipes.quote(converter.get_lang_opt()),
                includes=self.get_exported_include_tree(':' + name),
                deps=''.join([' ' + d for d in self.format_deps(cpp_deps)]),
                hdr=pipes.quote(hdr),
                src=pipes.quote(src)))
        rules.append(Rule('cxx_genrule', attrs))

        gen_hdr_name = gen_name + '=' + hdr
        rules.append(
            self.copy_rule(
                '$(location :{})/gen/{}'.format(gen_name, hdr),
                gen_hdr_name,
                hdr,
                propagate_versions=True))

        gen_src_name = gen_name + '=' + src
        rules.append(
            self.copy_rule(
                '$(location :{})/gen/{}'.format(gen_name, src),
                gen_src_name,
                src,
                propagate_versions=True))

        return (
            ':{}'.format(gen_name),
            ':' + gen_hdr_name,
            ':' + gen_src_name, rules)

    def generate_generated_source_rules(self, name, src_name, srcs, visibility):
        """
        Create rules to extra individual sources out of the directory of swig
        sources the compiler generated.
        """

        out = collections.OrderedDict()
        rules = []

        for sname, src in srcs.items():
            attrs = collections.OrderedDict()
            attrs['name'] = '{}={}'.format(name, src)
            if visibility is not None:
                attrs['visibility'] = visibility
            attrs['out'] = src
            attrs['cmd'] = ' && '.join([
                'mkdir -p `dirname $OUT`',
                'cp -rd $(location {})/lang/{} $OUT'.format(src_name, src),
            ])
            rules.append(Rule('cxx_genrule', attrs))
            out[sname] = ':' + attrs['name']

        return out, rules

    def convert_macros(
            self,
            base_path,
            name,
            interface,
            module=None,
            languages=(),
            swig_flags=(),
            cpp_deps=(),
            ext_deps=(),
            ext_external_deps=(),
            deps=(),
            visibility=None,
            **kwargs):
        """
        Swig library conversion implemented purely via macros (i.e. no Buck
        support).
        """

        rules = []

        # Parse incoming options.
        languages = self.get_languages(languages)
        cpp_deps = [target.parse_target(d, base_path) for d in cpp_deps]
        ext_deps = (
            [target.parse_target(d, base_path) for d in ext_deps] +
            [self.normalize_external_dep(d) for d in ext_external_deps])

        if module is None:
            module = name

        # Setup the exported include tree to dependents.
        rules.append(
            self.generate_merge_tree_rule(
                base_path,
                self.get_exported_include_tree(name),
                [interface],
                map(self.get_exported_include_tree, deps),
                visibility=visibility))

        # Generate rules for all supported languages.
        for lang in languages:
            converter = self._converters[lang]

            # Generate the swig compile rules.
            compile_rule, hdr, src, extra_rules = (
                self.generate_compile_rule(
                    base_path,
                    name,
                    swig_flags,
                    lang,
                    interface,
                    cpp_deps,
                    visibility=visibility,
                    **kwargs))
            rules.extend(extra_rules)

            # Create wrapper rules to extract individual generated sources
            # and expose via target refs in the UI.
            gen_srcs = converter.get_generated_sources(module)
            gen_srcs, gen_src_rules = (
                self.generate_generated_source_rules(
                    '{}-{}-src'.format(name, lang),
                    compile_rule,
                    gen_srcs,
                    visibility=visibility))
            rules.extend(gen_src_rules)

            # Generate the per-language rules.
            rules.extend(
                converter.get_language_rule(
                    base_path,
                    name + '-' + lang,
                    module,
                    hdr,
                    src,
                    gen_srcs,
                    sorted(set(cpp_deps + ext_deps)),
                    [dep + '-' + lang for dep in deps],
                    visibility=visibility,
                    **kwargs))

        return rules

    def get_allowed_args(self):
        """
        Return the list of allowed arguments.
        """

        allowed_args = {
            'cpp_deps',
            'ext_deps',
            'ext_external_deps',
            'deps',
            'interface',
            'java_library_name',
            'java_link_style',
            'java_package',
            'languages',
            'module',
            'name',
            'py_base_module',
            'swig_flags',
        }

        return allowed_args

    def convert(self, base_path, name=None, visibility=None, **kwargs):
        rules = []

        # Convert rules we support via macros.
        macro_languages = self.get_languages(kwargs.get('languages'))
        if macro_languages:
            rules.extend(self.convert_macros(base_path, name=name, visibility=visibility, **kwargs))

        return rules
