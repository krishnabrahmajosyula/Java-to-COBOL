#!/usr/bin/env python3
"""
Java seed extraction for Java -> COBOL translation.

Source : bigcode/the-stack-v2-dedup (Java)  -> blob contents fetched from the
         Software Heritage S3 bucket (needs AWS credentials).
Stages : 0. cheap metadata filter (size, vendor/generated)
         1. AST ALLOWLIST  (javalang)  -- every node must be on the allowlist
         2. javac compile
         3. java run (must exit 0 and print something)
         4. [optional] convert via the Antlr tool -> compile+run with GnuCOBOL ->
            compare Java vs COBOL output (comparison_log.csv)
Output : accepted_java/<blob_id>/<File>.java  (+ .in / .out / generated.cbl)
         accepted_log.csv, rejection_log.csv, comparison_log.csv

Setup:
    pip install datasets huggingface_hub python-dotenv javalang boto3 smart_open
    java + javac on PATH
    cobc (GnuCOBOL) on PATH  -- only needed if RUN_COBOL_VALIDATION is True
    Run this script from the root of the Java-to-COBOL repo (it expects an
    "Antlr" folder next to it, containing the tool's jars/classes -- see
    ANTLR_DIR / ANTLR_JARS / SCOPING_CLASS / PARSETREE_CLASS below).
    .env with:  HF_TOKEN=hf_xxx
                AWS_ACCESS_KEY_ID=...          (only needed if SOURCE == "swh")
                AWS_SECRET_ACCESS_KEY=...
    You must also have accepted the dataset terms on the HF page of
    bigcode/the-stack-v2-dedup.

NOTE ON STAGE 4: the entry-class rename (rename_entry_class) is a plain
whole-word regex substitution and is NOT verified against the real GUI's
findEntryClassName / rewriteEntryClassToTest. Replace it once you can share
those methods, especially if the real rewrite does anything beyond a literal
identifier swap.

Reference:
    Lozhkov et al., "StarCoder 2 and The Stack v2: The Next Generation",
    arXiv:2402.19173, 2024.
"""

import csv
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import javalang
from javalang import tree as T

# ============================ CONFIG ============================
SOURCE = "mirror"    # "mirror" = TempestTeam subset (content inline, no AWS needed)
                     # "swh"    = original bigcode/the-stack-v2-dedup + Software Heritage S3 (needs AWS keys)

MIRROR_DATASET = "TempestTeam/dataset-the-stack-v2-dedup-sub"
MIRROR_CONFIG = "Java"

HF_DATASET = "bigcode/the-stack-v2-dedup"
HF_CONFIG = "Java"
SWH_BUCKET = "softwareheritage"

OUTPUT_DIR = Path("accepted_java")
REJECTED_LOG = "rejection_log.csv"
ACCEPTED_LOG = "accepted_log.csv"
TARGET_ACCEPTED_COUNT = 1
MAX_ROWS_SCANNED = 2_000_000

MIN_BYTES, MAX_BYTES = 200, 15_000     # checked BEFORE downloading (saves S3 calls)
MIN_LINES, MAX_LINES = 5, 300

MAX_CLASSES = 3                        # main class + parent + child
ALLOW_UNRELATED_CLASSES = False        # extra classes must be in an extends relation
ALLOW_SUPER_METHOD_CALLS = False       # super.method()  (you only confirmed super(...))

RUN_TIMEOUT = 10
MAX_OUTPUT_CHARS = 20_000
# Deterministic values cycled in for each Scanner read, keyed by call type.
# (No more STDIN guessing -- see build_stdin_from_reads / check_scanner_inputs.)
SCANNER_VALUE_POOL = {
    "nextInt": ["7", "3", "9", "4", "12", "2", "15", "6"],
    "nextLong": ["7", "3", "9", "4", "12", "2", "15", "6"],
    "nextDouble": ["3.5", "7.25", "1.1", "9.9", "4.4", "2.2"],
    "nextFloat": ["3.5", "7.25", "1.1", "9.9", "4.4", "2.2"],
    "next": ["alpha", "beta", "gamma", "delta"],
    "nextLine": ["sample line one", "sample line two", "sample line three"],
}

WORK_BASE_DIR = os.path.expanduser("~/java2cob_work")   # javac temp files (not /tmp)

# ---- COBOL validation stage (runs AFTER Java acceptance, on accepted files only) ----
RUN_COBOL_VALIDATION = True

REPO_ROOT = Path(__file__).resolve().parent.parent      # script sits at the Java-to-COBOL repo root
print(f"Repo root dir: {REPO_ROOT}")
ANTLR_DIR = REPO_ROOT / "Antlr"                  # tool folder, per your setup
ANTLR_JARS = ["antlr-4.13.2-complete.jar", "javaparser-core-3.25.8.jar"]
SCOPING_CLASS = "TestJavaVariableScoping"
PARSETREE_CLASS = "ParseTreeGeneration"
FINAL_COBOL_NAME = "finalCobolCode.cbl"          # written by the tool inside ANTLR_DIR

TOOL_STEP_TIMEOUT = 40
COBOL_COMPILE_TIMEOUT = 40
COBOL_RUN_TIMEOUT = 10
GNUCOBOL_CMD = "cobc"

COBOL_WORK_BASE_DIR = os.path.expanduser("~/cobol_work")   # cobc compile/run scratch, own dir per run
COMPARISON_LOG = "comparison_log.csv"
LOG_TRUNCATE_CHARS = 800

# ASSUMPTION (unverified against the real GUI code): the tool needs its entry class
# named "Test" on disk (Test.java) but is told the ORIGINAL class name via argv, exactly
# as in the snippet you pasted. Renaming here is a plain whole-word identifier swap; it
# has NOT been checked against the real findEntryClassName / rewriteEntryClassToTest.
ENTRY_CLASS_NEW_NAME = "Test"

# ========================= ALLOWLIST TABLES =========================
ALLOWED_NODES = {
    # structure
    "CompilationUnit", "Import", "ClassDeclaration", "FieldDeclaration",
    "MethodDeclaration", "ConstructorDeclaration", "FormalParameter",
    "LocalVariableDeclaration", "VariableDeclaration", "VariableDeclarator",
    "BasicType", "ReferenceType", "Annotation",           # Annotation: @Override only
    # statements: if/else-if/else, loops, switch, break, return
    "IfStatement", "WhileStatement", "DoStatement", "ForStatement", "ForControl",
    "SwitchStatement", "SwitchStatementCase", "BreakStatement",
    "ReturnStatement", "BlockStatement", "StatementExpression",
    # expressions
    "Assignment", "BinaryOperation", "Cast", "Literal", "MemberReference",
    "MethodInvocation", "This", "ClassCreator", "SuperConstructorInvocation",
    "ArraySelector",                                       # only on String.split() results
}
if ALLOW_SUPER_METHOD_CALLS:
    ALLOWED_NODES.add("SuperMethodInvocation")

BASIC_TYPES = {"int", "long", "double", "float", "char", "boolean", "short", "byte"}
BIN_OPS = {"+", "-", "*", "/", "%", "<", ">", "<=", ">=", "==", "!=", "&&", "||"}
ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%="}
PREFIX_OPS = {"-", "+", "!", "++", "--"}
POSTFIX_OPS = {"++", "--"}
ALLOWED_MEMBER_MODS = {"public", "protected"}

SCANNER_METHODS = {"nextInt", "nextLine", "next", "nextDouble", "nextLong", "nextFloat", "close"}
STRING_METHODS = {
    "length", "charAt", "substring", "trim", "toUpperCase", "toLowerCase",
    "equals", "equalsIgnoreCase", "isEmpty", "indexOf", "startsWith",
    "endsWith", "concat", "split",
}
# Mirrors the intrinsic-function converter you attached. Add names here as the converter grows.
STATIC_CALLS = {
    "System": {"exit"},
    "System.out": {"print", "println"},
    "Math": {"abs", "min", "max", "sqrt", "sin", "tan", "asin", "acos", "atan",
             "exp", "floor", "log", "log10", "random", "signum", "floorMod"},
    "Integer": {"parseInt", "signum", "abs", "toString", "numberOfLeadingZeros"},
    "Long": {"parseLong", "abs", "toString"},
    "Double": {"parseDouble", "valueOf", "signum", "abs"},
    "Float": {"parseFloat", "signum", "abs"},
    "Short": {"parseShort", "abs"},
    "Byte": {"parseByte"},
    "String": {"valueOf"},
    "Character": {"codePointAt", "isDigit", "isLetter", "isUpperCase", "isLowerCase"},
}

FRIENDLY = {
    "TryStatement": "try/catch", "CatchClause": "try/catch", "ThrowStatement": "throw",
    "LambdaExpression": "a lambda", "MethodReference": "a method reference",
    "EnhancedForControl": "a for-each loop", "ArrayCreator": "array creation",
    "ArrayInitializer": "an array initializer", "TernaryExpression": "a ternary operator",
    "ContinueStatement": "continue", "InterfaceDeclaration": "an interface",
    "EnumDeclaration": "an enum", "AnnotationDeclaration": "an annotation type",
    "SynchronizedStatement": "synchronized", "AssertStatement": "assert",
    "ExplicitConstructorInvocation": "a this(...) constructor call",
    "SuperMethodInvocation": "a super.method() call", "SuperMemberReference": "super.field",
    "ClassReference": "a .class reference", "InnerClassCreator": "an inner class creation",
    "TypeArgument": "generics", "TypeParameter": "generics",
    "PackageDeclaration": "a package declaration",
}


# ========================= AST VALIDATOR =========================
def _sig(method):
    return tuple(p.type.name + "[]" * len(p.type.dimensions or []) for p in method.parameters)


def _chain(expr):
    return [expr] + list(getattr(expr, "selectors", None) or [])


def _ends_with_split(expr):
    if not isinstance(expr, T.Primary):
        return False
    last = _chain(expr)[-1]
    return isinstance(last, T.MethodInvocation) and last.member == "split"


class Validator:
    """validate(tree) -> (reason_or_None, meta)"""

    def __init__(self):
        self.classes = {}
        self.user_methods = set()
        self.var_types = {}
        self.split_arrays = set()
        self.main_class = None
        self.file_name = None
        self.uses_scanner = False
        self.has_output = False
        self.scanner_reads = []   # ordered list of call names, e.g. ["nextInt", "nextLine"]

    # ---- pass 1: file / class structure ----
    def check_structure(self, tree):
        if tree.package is not None:
            return "has a package declaration"
        for imp in tree.imports:
            ok = (not imp.static) and (
                (imp.path == "java.util.Scanner" and not imp.wildcard)
                or (imp.path == "java.util" and imp.wildcard))
            if not ok:
                return f"imports '{imp.path}' (only java.util.Scanner allowed)"

        for t in tree.types:
            if not isinstance(t, T.ClassDeclaration):
                return f"top-level {type(t).__name__} is not a plain class"
            if t.name in self.classes:
                return "duplicate top-level class names"
            self.classes[t.name] = t
        if not self.classes:
            return "no classes"
        if len(self.classes) > MAX_CLASSES:
            return f"{len(self.classes)} classes in one file (max {MAX_CLASSES})"

        mains, publics, main_params = [], [], set()
        for cname, cls in self.classes.items():
            if cls.implements:
                return f"class '{cname}' uses implements"
            if cls.type_parameters:
                return f"class '{cname}' is generic"
            extra = set(cls.modifiers) - {"public"}
            if extra:
                return f"class '{cname}' has class modifier {sorted(extra)}"
            if "public" in cls.modifiers:
                publics.append(cname)

            seen, ctors = set(), 0
            for mem in cls.body:
                if isinstance(mem, T.FieldDeclaration):
                    bad = set(mem.modifiers) - ALLOWED_MEMBER_MODS
                    if bad:
                        return f"field has modifier {sorted(bad)}"
                elif isinstance(mem, T.ConstructorDeclaration):
                    ctors += 1
                    if ctors > 1:
                        return "constructor overloading"
                    if mem.parameters:
                        return "constructor with parameters"
                    if set(mem.modifiers) - ALLOWED_MEMBER_MODS:
                        return "constructor with disallowed modifier"
                elif isinstance(mem, T.MethodDeclaration):
                    if mem.name == "main" and "static" in mem.modifiers:
                        p = mem.parameters[0] if len(mem.parameters) == 1 else None
                        ok = (set(mem.modifiers) == {"public", "static"}
                              and mem.return_type is None and p is not None
                              and p.type.name == "String" and p.type.dimensions)
                        if not ok:
                            return "non-standard main signature"
                        mains.append(cname)
                        main_params.add(p.name)
                        continue
                    bad = set(mem.modifiers) - ALLOWED_MEMBER_MODS
                    if bad:
                        return f"method has modifier {sorted(bad)}"
                    if mem.type_parameters:
                        return "generic method"
                    if mem.return_type is not None and mem.return_type.dimensions:
                        return "method returns an array"
                    for p in mem.parameters:
                        if p.type.dimensions or p.varargs:
                            return "method takes an array/varargs parameter"
                    if mem.name in seen:
                        return f"method overloading of '{mem.name}'"
                    seen.add(mem.name)
                    self.user_methods.add(mem.name)
                else:
                    kind = "an initializer block" if isinstance(mem, list) else type(mem).__name__
                    return f"class body contains {kind} (nested class / initializer)"

        if len(mains) != 1:
            return "no main method" if not mains else "multiple main methods"
        if len(publics) > 1:
            return "more than one public class"
        self.main_class = mains[0]
        self.file_name = publics[0] if publics else self.main_class
        self.split_arrays |= main_params

        # inheritance rules
        related = set()
        for cname, cls in self.classes.items():
            if cls.extends is None:
                continue
            pname = cls.extends.name
            if pname not in self.classes or pname == cname:
                return f"class '{cname}' extends '{pname}', which is not defined in this file"
            parent = self.classes[pname]
            if parent.extends is not None:
                return "multi-hop inheritance"
            related |= {cname, pname}
            if {d.name for f in cls.fields for d in f.declarators} & \
               {d.name for f in parent.fields for d in f.declarators}:
                return "child redeclares a parent field"
            psigs = {m.name: _sig(m) for m in parent.methods}
            for m in cls.methods:
                if m.name in psigs and psigs[m.name] != _sig(m):
                    return f"method '{m.name}' overloads a parent method"
        if not ALLOW_UNRELATED_CLASSES:
            for cname in self.classes:
                if cname != self.main_class and cname not in related:
                    return f"helper class '{cname}' is not part of an inheritance pair"
        return None

    # ---- pass 2: declarations, arrays, polymorphism ----
    def check_declarations(self, tree):
        decl_types = (T.LocalVariableDeclaration, T.FieldDeclaration, T.VariableDeclaration)
        for _, node in tree:
            if isinstance(node, decl_types):
                is_arr = bool(node.type.dimensions)
                for d in node.declarators:
                    self.var_types.setdefault(d.name, set()).add(node.type.name)
                    if is_arr or d.dimensions:
                        if is_arr and not d.dimensions and d.initializer is not None \
                                and _ends_with_split(d.initializer):
                            self.split_arrays.add(d.name)
                        else:
                            return "declares an array not produced by String.split"
            elif isinstance(node, T.FormalParameter):
                self.var_types.setdefault(node.name, set()).add(node.type.name)

        user = set(self.classes)

        def mismatch(declared, expr):
            if isinstance(expr, T.ClassCreator):
                return expr.type.name in user and expr.type.name not in declared
            if isinstance(expr, T.MemberReference) and not expr.qualifier and not expr.selectors:
                other = self.var_types.get(expr.member, set())
                return bool(other & user) and bool(declared & user) and declared.isdisjoint(other)
            return False

        for _, node in tree:
            if isinstance(node, decl_types):
                for d in node.declarators:
                    if d.initializer is not None and mismatch({node.type.name}, d.initializer):
                        return "polymorphism (parent-typed variable holds another class)"
            elif isinstance(node, T.Assignment) and node.type == "=":
                lhs = node.expressionl
                if isinstance(lhs, T.MemberReference) and not lhs.qualifier and not lhs.selectors:
                    if mismatch(self.var_types.get(lhs.member, set()), node.value):
                        return "polymorphism (assignment of a different class)"
        return None

    # ---- pass 2.5: Scanner reads -- reject loop-enclosed reads, record the rest in order ----
    def check_scanner_inputs(self, tree):
        LOOP_NODES = (T.WhileStatement, T.DoStatement, T.ForStatement)
        for path, node in tree:
            if not isinstance(node, T.MethodInvocation):
                continue
            if node.member not in SCANNER_METHODS or node.member == "close":
                continue
            q = node.qualifier
            if not q or "Scanner" not in self.var_types.get(q, set()):
                continue
            if any(isinstance(p, LOOP_NODES) for p in path):
                return ("reads from Scanner inside a loop "
                        "(input count can't be determined statically)")
            self.scanner_reads.append(node.member)
        return None

    # ---- pass 3: every node against the allowlist ----
    def check_node(self, node):
        name = type(node).__name__
        if name not in ALLOWED_NODES:
            return f"uses {FRIENDLY.get(name, name)}"
        if getattr(node, "label", None):
            return "uses a labeled statement"
        for op in getattr(node, "prefix_operators", None) or []:
            if op not in PREFIX_OPS:
                return f"uses prefix operator '{op}'"
        for op in getattr(node, "postfix_operators", None) or []:
            if op not in POSTFIX_OPS:
                return f"uses postfix operator '{op}'"

        if name in ("LocalVariableDeclaration", "VariableDeclaration", "FormalParameter") \
                and node.modifiers:
            return f"local/parameter has modifier {sorted(node.modifiers)}"
        if name == "BinaryOperation" and node.operator not in BIN_OPS:
            return f"uses operator '{node.operator}'"
        if name == "Assignment" and node.type not in ASSIGN_OPS:
            return f"uses assignment operator '{node.type}'"
        if name == "BasicType" and node.name not in BASIC_TYPES:
            return f"uses type '{node.name}'"
        if name == "ReferenceType":
            if node.name not in ({"String", "Scanner"} | set(self.classes)):
                return f"uses type '{node.name}'"
            if node.arguments or node.sub_type:
                return "uses a generic / qualified type"
        if name == "Cast" and node.type.dimensions:
            return "casts to an array type"
        if name == "Annotation" and node.name != "Override":
            return f"uses annotation '{node.name}'"
        if name == "SuperConstructorInvocation" and node.arguments:
            return "super(...) with arguments"
        if name == "ClassCreator":
            return self.check_creator(node)
        if name == "MemberReference":
            return self.check_member_ref(node)
        if name == "MethodInvocation":
            return self.check_call(node)
        if isinstance(node, T.Primary):
            return self.check_array_selectors(node)
        return None

    def check_creator(self, node):
        cname = node.type.name
        if node.body is not None:
            return "creates an anonymous class"
        if cname == "Scanner":
            a = node.arguments
            if len(a) == 1 and isinstance(a[0], T.MemberReference) \
                    and a[0].qualifier == "System" and a[0].member == "in":
                self.uses_scanner = True
                return None
            return "Scanner not created from System.in"
        if cname in self.classes:
            return "constructor call with arguments" if node.arguments else None
        return f"creates object of type '{cname}'"

    def check_member_ref(self, node):
        q = node.qualifier
        if q == "System":
            return None if node.member == "in" else f"uses 'System.{node.member}'"
        if q:
            first = q.split(".")[0]
            if first not in self.var_types:
                return f"references static/unknown '{first}'"
        return self.check_array_selectors(node)

    def check_call(self, node):
        q, m = node.qualifier, node.member
        if q == "System.out" and m in STATIC_CALLS["System.out"]:
            self.has_output = True
        if q in STATIC_CALLS:
            return None if m in STATIC_CALLS[q] else f"calls '{q}.{m}'"
        if not q:      # unqualified, or a chained call (selector) on a String/Scanner/object
            ok = m in (self.user_methods | STRING_METHODS | SCANNER_METHODS)
            return None if ok else f"calls unsupported method '{m}'"
        types = self.var_types.get(q, set())
        if "Scanner" in types and m in SCANNER_METHODS:
            return None
        if "String" in types and m in STRING_METHODS:
            return None
        if types & set(self.classes) and m in self.user_methods:
            return None
        return f"calls unsupported method '{q}.{m}'"

    def check_array_selectors(self, node):
        chain = _chain(node)
        for i, part in enumerate(chain):
            if isinstance(part, T.ArraySelector):
                if i == 0:
                    return "indexes an array"
                prev = chain[i - 1]
                if isinstance(prev, T.MemberReference) and i == 1 \
                        and not prev.qualifier and prev.member in self.split_arrays:
                    continue
                if isinstance(prev, T.MethodInvocation) and prev.member == "split":
                    continue
                return "indexes an array not produced by String.split"
        return None

    def validate(self, tree):
        r = self.check_structure(tree) or self.check_declarations(tree)
        if r:
            return r, {}
        r = self.check_scanner_inputs(tree)
        if r:
            return r, {}
        for _, node in tree:
            r = self.check_node(node)
            if r:
                return r, {}
        if not self.has_output:
            return "never prints with System.out.print/println", {}
        return None, {"main_class": self.main_class, "file_name": self.file_name,
                      "uses_scanner": self.uses_scanner, "scanner_reads": self.scanner_reads}


def ast_filter(source):
    """Returns (ok, reason, meta)."""
    try:
        tree = javalang.parse.parse(source)
    except Exception as e:
        return False, f"failed to parse ({type(e).__name__})", {}
    reason, meta = Validator().validate(tree)
    return (reason is None), (reason or "ok"), meta


def build_stdin_from_reads(scanner_reads):
    """Deterministic, type-correct stdin from the ordered list of Scanner call
    names found in the AST. Cycling values (rather than one fixed value)
    reduces the odds of hitting an accidental edge case like a divide-by-zero."""
    counters = Counter()
    tokens = []
    for call in scanner_reads:
        pool = SCANNER_VALUE_POOL.get(call, ["1"])
        tokens.append(pool[counters[call] % len(pool)])
        counters[call] += 1
    return ("\n".join(tokens) + "\n") if tokens else ""


# ========================= COMPILE + RUN =========================
def compile_and_run(workdir, file_name, main_class, stdin_text):
    """Returns (stage_failed_or_None, error, stdout).
    stdin_text is the single deterministic input built from the AST's Scanner
    reads (build_stdin_from_reads) -- no more guess-and-retry."""
    try:
        c = subprocess.run(["javac", "-encoding", "UTF-8", f"{file_name}.java"],
                           cwd=workdir, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=40)
    except subprocess.TimeoutExpired:
        return "java_compile", "javac timed out", None
    if c.returncode != 0:
        return "java_compile", c.stderr.strip()[:300], None

    try:
        r = subprocess.run(["java", "-Xmx256m", "-Xss2m", main_class],
                           cwd=workdir, input=stdin_text, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "java_run", "run timed out", None
    if r.returncode != 0:
        return "java_run", r.stderr.strip()[:300] or f"exit code {r.returncode}", None
    if not r.stdout.strip():
        return "java_run", "program produced no output", None
    if len(r.stdout) > MAX_OUTPUT_CHARS:
        return "java_run", "output too large", None
    return None, "", r.stdout


# ==================== STAGE 5: JAVA -> COBOL via the ANTLR tool ====================
def rename_entry_class(source, old_name, new_name=ENTRY_CLASS_NEW_NAME):
    """Whole-word rename of the entry class identifier everywhere it appears
    (declaration, constructors, any self-reference). UNVERIFIED stand-in for
    the real rewriteEntryClassToTest -- replace this once you share that method."""
    return re.sub(rf"\b{re.escape(old_name)}\b", new_name, source)


def extract_error_summary(combined_text, max_chars=None):
    """Java tools print stack traces/errors after whatever else they log (e.g.
    ParseTreeGeneration dumps the full parse tree before anything else), so a
    naive head-truncation shows only noise. This jumps to the first line that
    looks like a Java exception/error and takes the following ~40 lines
    (typical stack trace length); if none is found, falls back to the tail."""
    max_chars = max_chars or LOG_TRUNCATE_CHARS
    lines = combined_text.splitlines()
    marker_idx = next(
        (i for i, ln in enumerate(lines)
         if "Exception" in ln or "Error" in ln or ln.strip().startswith("at ")),
        None)
    snippet_lines = lines[marker_idx:marker_idx + 40] if marker_idx is not None else lines[-40:]
    return "\n".join(snippet_lines)[:max_chars]


def run_conversion_tool(java_source, entry_class_name, log_dir=None):
    """Mirrors the GUI's Run button: write Test.java, run the two tool jars from
    ANTLR_DIR, read back finalCobolCode.cbl. Returns (ok, cobol_source_or_None, log).
    The FULL combined output of both steps is always saved to
    log_dir/antlr_tool.log (when log_dir is given), regardless of outcome --
    the returned `log` string is only a short summary for the CSV."""
    test_path = ANTLR_DIR / "Test.java"
    cobol_path = ANTLR_DIR / FINAL_COBOL_NAME

    if not ANTLR_DIR.is_dir():
        return False, None, f"Antlr dir not found at {ANTLR_DIR}"
    cobol_path.unlink(missing_ok=True)   # avoid reading a stale file from a prior run

    rewritten = rename_entry_class(java_source, entry_class_name)
    test_path.write_text(rewritten, encoding="utf-8")

    cp_scope = os.pathsep.join(["."] + ANTLR_JARS)
    try:
        s1 = subprocess.run(["java", "-cp", cp_scope, SCOPING_CLASS, "Test.java"],
                            cwd=ANTLR_DIR, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=TOOL_STEP_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, None, f"{SCOPING_CLASS} timed out"

    cp_tree = os.pathsep.join(["."] + list(reversed(ANTLR_JARS)))
    s2 = None
    if s1.returncode == 0:
        try:
            s2 = subprocess.run(["java", "-cp", cp_tree, PARSETREE_CLASS, entry_class_name],
                                cwd=ANTLR_DIR, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=TOOL_STEP_TIMEOUT)
        except subprocess.TimeoutExpired:
            return False, None, f"{PARSETREE_CLASS} timed out"

    full_log = (f"=== {SCOPING_CLASS} (exit {s1.returncode}) ===\n{s1.stdout}{s1.stderr}\n\n"
                + (f"=== {PARSETREE_CLASS} (exit {s2.returncode}) ===\n{s2.stdout}{s2.stderr}\n"
                   if s2 is not None else f"=== {PARSETREE_CLASS} not run ===\n"))
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        (Path(log_dir) / "antlr_tool.log").write_text(full_log, encoding="utf-8")

    if s1.returncode != 0:
        return False, None, f"{SCOPING_CLASS} failed: " + extract_error_summary(s1.stdout + s1.stderr)
    if s2.returncode != 0:
        return False, None, f"{PARSETREE_CLASS} failed: " + extract_error_summary(s2.stdout + s2.stderr)
    if not cobol_path.exists():
        return False, None, (f"{PARSETREE_CLASS} exited 0 but never wrote {FINAL_COBOL_NAME} "
                              f"(likely an unsupported construct it silently skipped -- "
                              f"see antlr_tool.log)")

    try:
        cobol_source = cobol_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return False, None, f"could not read {FINAL_COBOL_NAME}: {e}"
    if not cobol_source.strip():
        return False, None, f"{FINAL_COBOL_NAME} was empty"
    return True, cobol_source, "ok"


# ==================== STAGE 6: COMPILE + RUN THE COBOL ====================
def compile_and_run_cobol(cobol_source, stdin_text, log_dir=None):
    """Returns dict with compile_ok, run_ok, stdout, error. Full cobc/run output
    is saved to log_dir/cobol_compile.log and log_dir/cobol_run.log when given."""
    os.makedirs(COBOL_WORK_BASE_DIR, exist_ok=True)
    result = {"compile_ok": False, "run_ok": False, "stdout": "", "error": ""}
    with tempfile.TemporaryDirectory(dir=COBOL_WORK_BASE_DIR) as tmp:
        workdir = Path(tmp)
        src = workdir / "prog.cbl"
        src.write_text(cobol_source, encoding="utf-8")
        exe = workdir / ("prog.exe" if os.name == "nt" else "prog")

        try:
            c = subprocess.run([GNUCOBOL_CMD, "-x", "prog.cbl", "-o", exe.name],
                               cwd=workdir, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=COBOL_COMPILE_TIMEOUT)
        except subprocess.TimeoutExpired:
            result["error"] = "cobc timed out"
            return result
        except FileNotFoundError:
            result["error"] = f"'{GNUCOBOL_CMD}' not found on PATH -- is GnuCOBOL installed?"
            return result
        if log_dir:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            (Path(log_dir) / "cobol_compile.log").write_text(
                f"exit {c.returncode}\n{c.stdout}{c.stderr}", encoding="utf-8")
        if c.returncode != 0 or not exe.exists():
            result["error"] = extract_error_summary(c.stdout + c.stderr) or "cobc compile failed"
            return result
        result["compile_ok"] = True

        run_cmd = [str(exe)] if os.name == "nt" else [f"./{exe.name}"]
        try:
            r = subprocess.run(run_cmd, cwd=workdir, input=stdin_text, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=COBOL_RUN_TIMEOUT)
        except subprocess.TimeoutExpired:
            result["error"] = "cobol run timed out"
            return result
        if log_dir:
            (Path(log_dir) / "cobol_run.log").write_text(
                f"exit {r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}", encoding="utf-8")
        if r.returncode != 0:
            result["error"] = extract_error_summary(r.stderr) or f"exit code {r.returncode}"
            return result
        result["run_ok"] = True
        result["stdout"] = r.stdout
    return result


def validate_with_cobol(file_label, java_source, meta, java_stdin, java_output, log_dir=None):
    """Runs stages 5+6 for one accepted program. Returns (row, cobol_source_or_None)
    where row has exactly: file, java_compile, cobol_compile, java_output, cobol_output.
    Wherever a stage has no real output, the column holds the error instead.
    Full tool/compiler/run logs are saved under log_dir when given -- the CSV
    only ever holds a short summary, never the whole trace."""
    entry_class = meta["file_name"]
    row = {
        "file": file_label,
        "java_compile": "ok",          # reaching here means javac+java already succeeded
        "cobol_compile": "",
        "java_output": java_output[:LOG_TRUNCATE_CHARS],
        "cobol_output": "",
    }

    tool_ok, cobol_source, tool_log = run_conversion_tool(java_source, entry_class, log_dir)
    if not tool_ok:
        err = f"conversion tool failed: {tool_log} [full log: antlr_tool.log]"[:LOG_TRUNCATE_CHARS]
        row["cobol_compile"] = err
        row["cobol_output"] = err
        return row, None

    cres = compile_and_run_cobol(cobol_source, java_stdin, log_dir)
    if not cres["compile_ok"]:
        err = (cres["error"] or "cobc compile failed") + " [full log: cobol_compile.log]"
        row["cobol_compile"] = err[:LOG_TRUNCATE_CHARS]
        row["cobol_output"] = err[:LOG_TRUNCATE_CHARS]
        return row, cobol_source

    row["cobol_compile"] = "ok"
    if cres["run_ok"]:
        row["cobol_output"] = cres["stdout"][:LOG_TRUNCATE_CHARS]
    else:
        err = (cres["error"] or "cobol run failed") + " [full log: cobol_run.log]"
        row["cobol_output"] = err[:LOG_TRUNCATE_CHARS]

    return row, cobol_source


# ========================= STACK V2 STREAMING =========================
def stream_mirror(hf_token):
    """TempestTeam mirror: file contents are already in the 'content' column."""
    from datasets import load_dataset

    print(f"Connecting to {MIRROR_DATASET} ({MIRROR_CONFIG})...")
    ds = load_dataset(MIRROR_DATASET, name=MIRROR_CONFIG, split="train",
                      streaming=True, token=hf_token)
    for scanned, row in enumerate(ds, start=1):
        if scanned > MAX_ROWS_SCANNED:
            return
        if scanned % 1000 == 0:
            print(f"   ...scanned {scanned} rows")
        content = row.get("content")
        if not content:
            continue
        lines = content.count("\n") + 1
        if lines < MIN_LINES or lines > MAX_LINES:
            continue
        if not (MIN_BYTES <= len(content.encode("utf-8", "replace")) <= MAX_BYTES):
            continue
        cid = row.get("blob_id") or f"sample_{scanned:07d}"
        yield cid, row.get("repo_name", ""), row.get("path", ""), content


def stream_candidates(hf_token):
    return stream_mirror(hf_token) if SOURCE == "mirror" else stream_swh(hf_token)


def stream_swh(hf_token):
    import boto3
    from datasets import load_dataset
    from smart_open import open as smart_open

    s3 = boto3.Session(
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name="us-east-1",
    ).client("s3")

    def download(blob_id, encoding):
        url = f"s3://{SWH_BUCKET}/content/{blob_id}"
        with smart_open(url, "rb", compression=".gz", transport_params={"client": s3}) as f:
            return f.read().decode(encoding or "utf-8", errors="replace")

    print(f"Connecting to {HF_DATASET} ({HF_CONFIG})...")
    ds = load_dataset(HF_DATASET, name=HF_CONFIG, split="train", streaming=True, token=hf_token)

    for scanned, row in enumerate(ds, start=1):
        if scanned > MAX_ROWS_SCANNED:
            return
        if scanned % 1000 == 0:
            print(f"   ...scanned {scanned} rows")
        if row.get("is_vendor") or row.get("is_generated"):
            continue
        size = row.get("length_bytes") or 0
        if size < MIN_BYTES or size > MAX_BYTES:
            continue
        try:
            content = download(row["blob_id"], row.get("src_encoding"))
        except Exception as e:
            print(f"   download failed for {row['blob_id']}: {type(e).__name__}")
            continue
        lines = content.count("\n") + 1
        if lines < MIN_LINES or lines > MAX_LINES:
            continue
        yield row["blob_id"], row.get("repo_name", ""), row.get("path", ""), content


# ================================ MAIN ================================
def main():
    from dotenv import load_dotenv
    load_dotenv()
    hf_token = os.getenv("HF_TOKEN")
    if not hf_token:
        print("Need HF_TOKEN in .env")
        sys.exit(1)
    if SOURCE == "swh" and not (os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY")):
        print("SOURCE='swh' needs AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in .env")
        sys.exit(1)
    print(f"Source: {SOURCE}")

    OUTPUT_DIR.mkdir(exist_ok=True)
    os.makedirs(WORK_BASE_DIR, exist_ok=True)
    reasons = Counter()
    accepted = rejected = 0
    cobol_ok_count = 0

    comparison_writer = None
    comparison_file = None
    if RUN_COBOL_VALIDATION:
        comparison_file = open(COMPARISON_LOG, "w", newline="")
        comparison_writer = csv.writer(comparison_file)
        comparison_writer.writerow([
            "file", "java_compile", "cobol_compile", "java_output", "cobol_output",
        ])

    with open(REJECTED_LOG, "w", newline="") as rf, open(ACCEPTED_LOG, "w", newline="") as af:
        rw, aw = csv.writer(rf), csv.writer(af)
        rw.writerow(["blob_id", "repo_name", "path", "stage", "reason"])
        aw.writerow(["blob_id", "repo_name", "path", "file", "main_class", "uses_scanner"])

        for blob_id, repo, path, source in stream_candidates(hf_token):
            if accepted >= TARGET_ACCEPTED_COUNT:
                break

            def reject(stage, reason):
                nonlocal rejected
                rejected += 1
                reasons[f"{stage}: " + re.sub(r"'[^']*'|\[.*?\]", "'_'", reason)] += 1
                rw.writerow([blob_id, repo, path, stage, reason])
                rf.flush()

            ok, reason, meta = ast_filter(source)
            if not ok:
                reject("ast_allowlist", reason)
                continue

            stdin_text = build_stdin_from_reads(meta["scanner_reads"]) if meta["uses_scanner"] else ""
            with tempfile.TemporaryDirectory(dir=WORK_BASE_DIR) as tmp:
                workdir = Path(tmp)
                (workdir / f"{meta['file_name']}.java").write_text(source, encoding="utf-8")
                stage, err, out = compile_and_run(
                    workdir, meta["file_name"], meta["main_class"], stdin_text)
            if stage:
                reject(stage, err)
                continue

            dest = OUTPUT_DIR / blob_id
            dest.mkdir(exist_ok=True)
            (dest / f"{meta['file_name']}.java").write_text(source, encoding="utf-8")
            (dest / "expected.out").write_text(out, encoding="utf-8")
            if meta["uses_scanner"]:
                (dest / "stdin.in").write_text(stdin_text, encoding="utf-8")
            aw.writerow([blob_id, repo, path, f"{meta['file_name']}.java",
                         meta["main_class"], meta["uses_scanner"]])
            af.flush()
            accepted += 1
            print(f"ACCEPTED {accepted}/{TARGET_ACCEPTED_COUNT}: {blob_id} ({meta['main_class']})")

            if RUN_COBOL_VALIDATION:
                file_label = f"{blob_id}/{meta['file_name']}.java"
                row, cobol_source = validate_with_cobol(
                    file_label, source, meta, stdin_text, out, log_dir=dest)
                if cobol_source:
                    (dest / "generated.cbl").write_text(cobol_source, encoding="utf-8")
                comparison_writer.writerow([
                    row["file"], row["java_compile"], row["cobol_compile"],
                    row["java_output"], row["cobol_output"],
                ])
                comparison_file.flush()
                if row["cobol_compile"] == "ok":
                    cobol_ok_count += 1
                print(f"   COBOL: compile={row['cobol_compile'][:60]!r} "
                      f"output={row['cobol_output'][:60]!r}")

    if comparison_file:
        comparison_file.close()

    print(f"\n=== DONE: {accepted} accepted, {rejected} rejected ===")
    if RUN_COBOL_VALIDATION:
        print(f"=== COBOL: {cobol_ok_count}/{accepted} compiled+ran "
              f"(see {COMPARISON_LOG} to compare outputs by eye) ===")
    print("Top rejection reasons:")
    for reason, n in reasons.most_common(15):
        print(f"  {n:6d}  {reason}")


if __name__ == "__main__":
    main()
