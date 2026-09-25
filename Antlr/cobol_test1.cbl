       IDENTIFICATION DIVISION.
       PROGRAM-ID. PostPreFixOp.


       DATA DIVISION.
       WORKING-STORAGE SECTION.
      * Variables for Scope:METHOD_MAIN_CLASS_TEST_GLOBAL
       01  args_main-ARRAY.
           05  args_main    PIC X(100) OCCURS 100 TIMES.
       01  args_main-MAX   PIC S9(9) VALUE 100.
      * Variables for Scope:BLOCK_METHOD_MAIN_CLASS_TEST_GLOBAL
       01  a_main          PIC S9(9).
       01  b_main          PIC S9(9).
       01  prefix_main     PIC S9(9).
       01  postfix_main    PIC S9(9).
