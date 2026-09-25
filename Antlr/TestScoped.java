class Test {

    public static void main(String[] args_main) {
        int a_main = 10, b_main = 10;
        System.out.println("\n------ Before ------");
        System.out.println("Value of a before: " + a_main);
        System.out.println("Value of b before: " + b_main);
        // prefix increment operator
        int prefix_main = ++a_main;
        // postfix increment operator
        int postfix_main = b_main++;
        System.out.println("\n------ After ------");
        System.out.println("prefix value: " + prefix_main);
        System.out.println("Value of a now: " + a_main);
        System.out.println("postfix value: " + postfix_main);
        System.out.println("Value of b now: " + b_main);
    }
}
