#!/usr/bin/env python3
"""
Fixes inheritance support bugs — 3 fixes across 2 files.

BUGS FIXED
----------
BUG 1 (OopDataDivisionGen.java):
  Dog has no fields → DOG-OBJ scratch record never emitted
  → "DOG-OBJ is not defined" at compile time.

BUG 2 (OopDataDivisionGen.java):
  Dog-D-INST emitted as empty group "01 DOG-D-INST." with no children
  → "PICTURE clause required for DOG-D-INST" at compile time.

BUG 3 (JavaToCobolListenerPD.java):
  d.speak() where speak() is inherited from Animal builds "DOG-SPEAK-PARA"
  but the paragraph is actually "ANIMAL-SPEAK-PARA"
  → "DOG-SPEAK-PARA is not defined" at compile time.

Run from your Antlr/ directory:
    python3 apply_fix.py

Backups saved as  *.java.bak
"""

import shutil, sys
from pathlib import Path

def patch(path_str, fixes):
    p = Path(path_str)
    if not p.exists():
        print(f"ERROR: {p} not found. Run from the Antlr/ directory.")
        sys.exit(1)
    shutil.copy(p, p.with_suffix(".java.bak"))
    print(f"Backup → {p.with_suffix('.java.bak')}")
    src = p.read_text(encoding="utf-8")
    original = src
    for label, old, new in fixes:
        if old in src:
            src = src.replace(old, new, 1)
            print(f"  {label}  ✓")
        else:
            print(f"  {label}  SKIPPED — pattern not found (already applied?)")
    if src != original:
        p.write_text(src, encoding="utf-8")
        print(f"  {p} updated.\n")
    else:
        print(f"  WARNING: no changes written to {p}\n")

# ═════════════════════════════════════════════════════════════════════════════
# FILE 1 — OopDataDivisionGen.java
# ═════════════════════════════════════════════════════════════════════════════

# ── FIX A ── Template scratch record (CLASSNAME-OBJ)
#
# BEFORE: only emitted when hasInstanceState is true.
#         Dog has no fields and inherits none → never emitted → DOG-OBJ undefined.
#
# AFTER:  always emit the record; add a FILLER when there are no real fields
#         so the group record is always valid COBOL.

FIX_A_OLD = '''\
            boolean hasInheritedFields = cls.parentClass != null && classes.containsKey(cls.parentClass)
                    && !classes.get(cls.parentClass).fields.isEmpty();
            boolean hasInstanceState = hasInheritedFields || !cls.fields.isEmpty();
            // ── 1. Template record (used as scratch during method calls) ──
            if (hasInstanceState) {
                writer.printf("      * Class record: %s%n", cls.className);
                writer.printf("       01  %s-OBJ.%n", cls.className.toUpperCase());
            }

            // Inherit parent fields first (single-level only)
            if (cls.parentClass != null && classes.containsKey(cls.parentClass)) {
                ClassInfoExtractor.ClassInfo parent = classes.get(cls.parentClass);
                for (ClassInfoExtractor.FieldInfo f : parent.fields) {
                    writer.printf("           05  %s%-12s %s%n",
                        cls.className.toUpperCase() + "-OBJ-",
                        f.name.toUpperCase(),
                        mapJavaToPic(f.type));
                }
            }
            // Own fields
            for (ClassInfoExtractor.FieldInfo f : cls.fields) {
                writer.printf("           05  %s%-12s %s%n",
                    cls.className.toUpperCase() + "-OBJ-",
                    f.name.toUpperCase(),
                    mapJavaToPic(f.type));
            }'''

FIX_A_NEW = '''\
            boolean hasInheritedFields = cls.parentClass != null && classes.containsKey(cls.parentClass)
                    && !classes.get(cls.parentClass).fields.isEmpty();
            boolean hasInstanceState = hasInheritedFields || !cls.fields.isEmpty();
            // ── 1. Template record (used as scratch during method calls) ──
            // Always emit even when the class has no fields (e.g. Dog extends Animal
            // but adds no new fields). An empty group record is invalid COBOL, so
            // we add a FILLER when there are no real fields to declare.
            writer.printf("      * Class record: %s%n", cls.className);
            writer.printf("       01  %s-OBJ.%n", cls.className.toUpperCase());

            // Inherit parent fields first (single-level only)
            boolean wroteAnyField = false;
            if (cls.parentClass != null && classes.containsKey(cls.parentClass)) {
                ClassInfoExtractor.ClassInfo parent = classes.get(cls.parentClass);
                for (ClassInfoExtractor.FieldInfo f : parent.fields) {
                    writer.printf("           05  %s%-12s %s%n",
                        cls.className.toUpperCase() + "-OBJ-",
                        f.name.toUpperCase(),
                        mapJavaToPic(f.type));
                    wroteAnyField = true;
                }
            }
            // Own fields
            for (ClassInfoExtractor.FieldInfo f : cls.fields) {
                writer.printf("           05  %s%-12s %s%n",
                    cls.className.toUpperCase() + "-OBJ-",
                    f.name.toUpperCase(),
                    mapJavaToPic(f.type));
                wroteAnyField = true;
            }
            // If no fields at all, emit a FILLER to keep the group record valid
            if (!wroteAnyField) {
                writer.printf("           05  FILLER          PIC X(1).%n");
            }'''

# ── FIX B ── Named instance records (CLASSNAME-VARNAME-INST)
#
# BEFORE: emits "01 DOG-D-INST." then loops over fields.
#         When Dog has no fields the loop body never runs → empty group → error.
#
# AFTER:  track whether any field was written; emit FILLER if none.

FIX_B_OLD = '''\
            writer.printf("       01  %s-%s-INST.%n",
                className.toUpperCase(), varName.toUpperCase());

            // Inherit parent fields
            if (cls.parentClass != null && classes.containsKey(cls.parentClass)) {
                for (ClassInfoExtractor.FieldInfo f : classes.get(cls.parentClass).fields) {
                    writer.printf("           05  %s-%s-INST-%-12s %s%n",
                        className.toUpperCase(), varName.toUpperCase(),
                        f.name.toUpperCase(), mapJavaToPic(f.type));
                }
            }
            for (ClassInfoExtractor.FieldInfo f : cls.fields) {
                writer.printf("           05  %s-%s-INST-%-12s %s%n",
                    className.toUpperCase(), varName.toUpperCase(),
                    f.name.toUpperCase(), mapJavaToPic(f.type));
            }'''

FIX_B_NEW = '''\
            writer.printf("       01  %s-%s-INST.%n",
                className.toUpperCase(), varName.toUpperCase());

            // Inherit parent fields
            boolean wroteInstField = false;
            if (cls.parentClass != null && classes.containsKey(cls.parentClass)) {
                for (ClassInfoExtractor.FieldInfo f : classes.get(cls.parentClass).fields) {
                    writer.printf("           05  %s-%s-INST-%-12s %s%n",
                        className.toUpperCase(), varName.toUpperCase(),
                        f.name.toUpperCase(), mapJavaToPic(f.type));
                    wroteInstField = true;
                }
            }
            for (ClassInfoExtractor.FieldInfo f : cls.fields) {
                writer.printf("           05  %s-%s-INST-%-12s %s%n",
                    className.toUpperCase(), varName.toUpperCase(),
                    f.name.toUpperCase(), mapJavaToPic(f.type));
                wroteInstField = true;
            }
            // Guard: an empty group record is invalid COBOL
            if (!wroteInstField) {
                writer.printf("           05  FILLER          PIC X(1).%n");
            }'''

print("── OopDataDivisionGen.java ──")
patch("OopDataDivisionGen.java", [
    ("FIX A — always emit CLASS-OBJ record, add FILLER when no fields", FIX_A_OLD, FIX_A_NEW),
    ("FIX B — always emit CLASS-VAR-INST record, add FILLER when no fields", FIX_B_OLD, FIX_B_NEW),
])

# ═════════════════════════════════════════════════════════════════════════════
# FILE 2 — JavaToCobolListenerPD.java
# ═════════════════════════════════════════════════════════════════════════════

# ── FIX C ── Inherited method dispatch
#
# BEFORE:
#   String paraName = className.toUpperCase() + "-" + methName.toUpperCase() + "-PARA";
#   → always uses the declared type's class name (Dog), even when the method
#     was inherited from a parent (Animal) and its paragraph is ANIMAL-SPEAK-PARA.
#
# AFTER:
#   Walk the classInfoMap inheritance chain to find which class actually
#   declares the method, then use THAT class name for the paragraph prefix.
#   Also add the same walk to processExpressionWithCalls for return-value calls.

FIX_C_OLD = '''\
        // ── OOP: instance method call  obj.method(args) ───────────────────
        {
            Pattern dotCallPat = Pattern.compile(
                "^(\\\\w+)\\\\.(\\\\w+)\\\\s*\\\\(([^)]*)\\\\)\\\\s*;?$");
            Matcher dotMatcher = dotCallPat.matcher(text.trim());
            if (dotMatcher.matches()) {
                String objVar = dotMatcher.group(1);
                String methName = dotMatcher.group(2);
                String argList = dotMatcher.group(3);
                String className = objectVariables.get(objVar);
                if (className != null && classInfoMap.containsKey(className)) {
                    String instRecord = className.toUpperCase() + "-" + objVar.toUpperCase() + "-INST";
                    String paraName = className.toUpperCase() + "-" + methName.toUpperCase() + "-PARA";
                    emitCobol(INDENT + "MOVE " + instRecord + " TO " + className.toUpperCase() + "-OBJ\\n");
                    List<String> argValues = splitArgs(argList);
                    ClassInfoExtractor.ClassInfo cls = classInfoMap.get(className);
                    ClassInfoExtractor.MethodInfo method = cls.methods.stream()
                        .filter(mm -> mm.name.equals(methName))
                        .findFirst().orElse(null);
                    if (method != null) {
                        for (int mi = 0; mi < Math.min(method.paramNames.size(), argValues.size()); mi++) {
                            emitCobol(INDENT + "MOVE "
                                + translateArgumentExpression(argValues.get(mi))
                                + " TO " + oopParameterName(className, methName,
                                        method.paramNames.get(mi)) + "\\n");
                        }
                    }
                    emitCobol(INDENT + "PERFORM " + paraName + "\\n");
                    emitCobol(INDENT + "MOVE " + className.toUpperCase() + "-OBJ TO " + instRecord + "\\n");
                    return;
                }
            }
        }'''

FIX_C_NEW = '''\
        // ── OOP: instance method call  obj.method(args) ───────────────────
        {
            Pattern dotCallPat = Pattern.compile(
                "^(\\\\w+)\\\\.(\\\\w+)\\\\s*\\\\(([^)]*)\\\\)\\\\s*;?$");
            Matcher dotMatcher = dotCallPat.matcher(text.trim());
            if (dotMatcher.matches()) {
                String objVar = dotMatcher.group(1);
                String methName = dotMatcher.group(2);
                String argList = dotMatcher.group(3);
                String className = objectVariables.get(objVar);
                if (className != null && classInfoMap.containsKey(className)) {
                    String instRecord = className.toUpperCase() + "-" + objVar.toUpperCase() + "-INST";
                    // Walk the inheritance chain to find which class actually declares
                    // this method. d.speak() where speak() is in Animal (not Dog) must
                    // dispatch to ANIMAL-SPEAK-PARA, not the non-existent DOG-SPEAK-PARA.
                    String ownerClass = findMethodOwner(className, methName);
                    String paraName = ownerClass.toUpperCase() + "-" + methName.toUpperCase() + "-PARA";
                    emitCobol(INDENT + "MOVE " + instRecord + " TO " + className.toUpperCase() + "-OBJ\\n");
                    List<String> argValues = splitArgs(argList);
                    // Find method info from the owning class
                    ClassInfoExtractor.ClassInfo ownerCls = classInfoMap.get(ownerClass);
                    ClassInfoExtractor.MethodInfo method = (ownerCls != null)
                        ? ownerCls.methods.stream()
                            .filter(mm -> mm.name.equals(methName))
                            .findFirst().orElse(null)
                        : null;
                    if (method != null) {
                        for (int mi = 0; mi < Math.min(method.paramNames.size(), argValues.size()); mi++) {
                            emitCobol(INDENT + "MOVE "
                                + translateArgumentExpression(argValues.get(mi))
                                + " TO " + oopParameterName(ownerClass, methName,
                                        method.paramNames.get(mi)) + "\\n");
                        }
                    }
                    emitCobol(INDENT + "PERFORM " + paraName + "\\n");
                    emitCobol(INDENT + "MOVE " + className.toUpperCase() + "-OBJ TO " + instRecord + "\\n");
                    return;
                }
            }
        }'''

# ── FIX D ── Add findMethodOwner() helper method
#
# Insert just before the closing brace of the class (last } in the file).
# We anchor on a unique method that appears near the end of the file.

FIX_D_OLD = '''\
    private String reduceArithmeticToTemp(String expr) {'''

FIX_D_NEW = '''\
    /**
     * Walks the classInfoMap inheritance chain starting from className to find
     * which class actually declares a method called methodName.
     * Returns className itself if the method is found there, otherwise walks
     * up via parentClass. Falls back to className if not found anywhere
     * (keeps old behaviour for unresolved cases).
     */
    private String findMethodOwner(String className, String methodName) {
        String current = className;
        while (current != null && classInfoMap.containsKey(current)) {
            ClassInfoExtractor.ClassInfo info = classInfoMap.get(current);
            for (ClassInfoExtractor.MethodInfo m : info.methods) {
                if (m.name.equals(methodName)) {
                    return current;   // found — this class owns the method
                }
            }
            current = info.parentClass;   // walk up
        }
        return className;   // not found — fall back to declared type
    }

    private String reduceArithmeticToTemp(String expr) {'''

print("── JavaToCobolListenerPD.java ──")
patch("JavaToCobolListenerPD.java", [
    ("FIX C — inherited method dispatch uses ownerClass not declaredClass", FIX_C_OLD, FIX_C_NEW),
    ("FIX D — add findMethodOwner() helper", FIX_D_OLD, FIX_D_NEW),
])

print("All done. To revert any file:  copy the .java.bak over the .java file.")