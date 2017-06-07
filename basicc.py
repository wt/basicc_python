#!/usr/bin/env python
import argparse
import os
import os.path
import pprint
import shlex
import subprocess

import gccjit

import tatsu
import tatsu.model

GRAMMAR="""\
    @@grammar:: BASIC

    program::Program = {line}+ ;

    line::Line =
        | line_number:line_number statement:statement ({})
        | line_number:line_number ({})
        ;

    line_number = number ;

    statement =
        | comment_statement
        | print_statement
        ;

    comment_statement::CommentStatement = "REM" / / comment:/[^\n]*/;

    print_statement::PrintStatement = "PRINT" expression_list:expression_list ;

    expression_list = ",".{label_expression}+ ;

    label_expression = label:[label] expression:expression ;

    label = string ;

    expression =
        | number:number
        | string:string
        ;

    number = /\d+/ ;

    string = '"' value:/[^"]*/ '"' ;
"""


class BasicProgramCompiler(object):
    def __init__(self):
        self.ctx = gccjit.Context()
        self.main_fn, self.argc, self.argv = gccjit.make_main(self.ctx)
        self.main_block = self.main_fn.new_block(b"entry")
        self.debug_mode = False

    def add_statement(self, statement, line_number):
        statement.add_isn_to_program(self, line_number)

    def set_debug_mode(self, val):
        self.debug_mode = val
        self.ctx.set_bool_option(gccjit.BoolOption.DEBUGINFO, val)

    def compile(self, namebase, outfile):
        full_object_filename = "{}.o".format(namebase)
        int_type = self.ctx.get_type(gccjit.TypeKind.INT)
        self.main_block.end_with_return(self.ctx.zero(int_type))
        self.ctx.compile_to_file(gccjit.OutputKind.OBJECT_FILE, full_object_filename.encode())

        args = ["gcc"]
        if self.debug_mode:
            args.append("-g")

        args.extend(["-o", outfile])
        args.extend([full_object_filename, "-lm"])

        proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.check_returncode()

        os.remove(full_object_filename)


class ModelTypeMeta(type):
    def __init__(cls, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not hasattr(cls, "registry"):
            cls.registry = set()
        cls.registry.add(cls)


class ModelType(metaclass=ModelTypeMeta):
    def __init__(self, *args, **kwargs):
        import pdb; pdb.set_trace()


class Program(ModelType):
    def __init__(self, lines):
        self.lines = lines


class Statement(ModelType):
    def __init__(self, *args, **kwargs):
        pass

    def __str__(self):
       return "{} {}".format(self.instruction_name, self._args_str)

    def __repr__(self):
        return self.__str__()


class CommentStatement(Statement):
    instruction_name = "REM"

    def __init__(self, node):
        super().__init__()
        self.comment = node["comment"]

    def get_args_str(self):
        return self.comment

    _args_str = property(get_args_str)

    def add_isn_to_program(self, program, line_number):
        program.main_block.add_comment(self.comment.encode())


class PrintStatement(Statement):
    instruction_name = "PRINT"

    def __init__(self, node):
        super().__init__()
        self.expressions = []
        for e in node["expression_list"]:
            self.expressions.append(NumberExpression(e.label, e.expression.number))
        #import pdb; pdb.set_trace()

    def get_args_str(self):
        return ", ".join([str(x) for x in self.expressions])

    _args_str = property(get_args_str)

    def add_isn_to_program(self, program, line_number):
        int_type = program.ctx.get_type(gccjit.TypeKind.INT)
        char_ptr_type = program.ctx.get_type(gccjit.TypeKind.CHAR).get_pointer()
        param_format = program.ctx.new_param(char_ptr_type, b"format")
        printf_fn = program.ctx.new_function(
            gccjit.FunctionKind.IMPORTED, int_type, b"printf", [param_format],
            is_variadic=True)

        if len(self.expressions) > 0:
            printf_args = [program.ctx.new_string_literal(b" ".join([b"%s"]*len(self.expressions)) + b"\n")]

            for e in self.expressions:
                printf_args.append(program.ctx.new_string_literal(e.get_str_for_print().encode()))

            local_i = program.main_fn.new_local(int_type, "printf.line.{}".format(line_number).encode())
            printf_call = program.ctx.new_call(printf_fn, printf_args)
            program.main_block.add_assignment(local_i, printf_call)
            # TODO(wt): check return code of printf


class Expression(object):
    def __init__(self, label):
        self.label = label


class NumberExpression(Expression):
    def __init__(self, label, number):
        super().__init__(label)
        self.number = number

    def __str__(self):
        if self.label is None:
            return str(self.number)
        else:
            return '"{}" {}'.format(str(self.label.value), str(self.number))

    def get_str_for_print(self):
        if self.label is None:
            return str(self.number)
        else:
            return "{}{}".format(str(self.label.value), str(self.number))


class MyNodeWalker(tatsu.model.DepthFirstWalker):
    def walk_Program(self, node, children, basic_program):
        statements = {}
        line_num_max_digits = 0
        for line in node.lines:
            #print(line)
            line_num = line.line_number
            print(line_num)
            line_num_max_digits = max(line_num_max_digits, len(line_num))
            try:
                print(line.statement)
                statements[int(line_num)] = line.statement
            except AttributeError:
                statements.pop(int(line_num), "don't care")

        for k in sorted(statements.keys()):
            basic_program.add_statement(statements[k], k)


def get_prog_text(srcfilename):
    with open(srcfilename) as srcfile:
        return srcfile.read()

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("-g", dest="debug", action="store_true")
    parser.add_argument("-o", dest="outfile", default="a.out")
    parser.add_argument("-v", dest="verbose", action="count")
    parser.add_argument("srcfile")
    return parser.parse_args()


def main():
    args = parse_args()
    program_text = get_prog_text(args.srcfile)

    print(args)
    model_parser = tatsu.compile(GRAMMAR, semantics=tatsu.model.ModelBuilderSemantics(types=ModelType.registry))
    model = model_parser.parse(program_text)

    program = BasicProgramCompiler()
    if args.debug:
        program.set_debug_mode(True)
    if args.verbose:
        program.ctx.set_bool_option(gccjit.BoolOption.DUMP_INITIAL_GIMPLE, True)
    walker = MyNodeWalker()
    walker.walk(model, program)

    src_namebase = os.path.splitext(args.srcfile)[0]
    program.compile(src_namebase, args.outfile)

    #########################################################
    #double_type = ctx.get_type(gccjit.TypeKind.DOUBLE)
    #param_x = ctx.new_param(double_type, b"x")
    #sqrtf_fn = ctx.new_function(gccjit.FunctionKind.IMPORTED,
    #                           double_type,
    #                           b"sqrtf",
    #                           [param_x])

    #local_i = main_fn.new_local(double_type, b"i")
    #main_block.add_assignment(local_i, ctx.new_rvalue_from_double(double_type, 16))
    #str_arg = ctx.new_string_literal(b"%f blah\n")
    #printf_call = ctx.new_call(printf_fn, [str_arg, local_i])
    #main_block.add_eval(printf_call)

    #double_arg = ctx.new_rvalue_from_double(double_type, 4)
    #sqrtf_call = ctx.new_call(sqrtf_fn, [double_arg])
    #main_block.add_assignment(local_i, sqrtf_call)
    #########################################################


if __name__ == "__main__":
    main()
