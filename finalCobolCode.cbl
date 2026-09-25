       IDENTIFICATION DIVISION.
       PROGRAM-ID. PostPreFixOp.


       DATA DIVISION.
       WORKING-STORAGE SECTION.
      * Variables for Scope:METHOD_MAIN_CLASS_POSTPREFIXOP_GLOBAL
       01  args_main-ARRAY.
           05  args_main    PIC X(100) OCCURS 100 TIMES.
       01  args_main-MAX   PIC S9(9) VALUE 100.
      * Variables for Scope:BLOCK_METHOD_MAIN_CLASS_POSTPREFIXOP_GLOBAL
       01  a_main          PIC S9(9).
       01  b_main          PIC S9(9).
       01  prefix_main     PIC S9(9).
       01  postfix_main    PIC S9(9).
      * Class record: PostPreFixOp
       01  POSTPREFIXOP-OBJ.
           05  FILLER          PIC X(1).
      * Object instance records
      * OOP method parameters


       PROCEDURE DIVISION.


       ENTRY-PARA.
           PERFORM MAIN-PARA
           STOP RUN.


       MAIN-PARA.
       DISPLAY "\n------ Before ------"
       DISPLAY "Value of a before: "
       DISPLAY a_main
       DISPLAY "Value of b before: "
       DISPLAY b_main
      * prefix increment operator
       ADD 1 TO a_main.
       COMPUTE prefix_main = a_main
      * postfix increment operator
       COMPUTE postfix_main = b_main
       ADD 1 TO b_main.
       DISPLAY "\n------ After ------"
       DISPLAY "prefix value: "
       DISPLAY prefix_main
       DISPLAY "Value of a now: "
       DISPLAY a_main
       DISPLAY "postfix value: "
       DISPLAY postfix_main
       DISPLAY "Value of b now: "
       DISPLAY b_main
       EXIT.


