// The APEXlang tree as the PL/SQL blocks that install it (ADT #1071).
//
// SQLcl's `apex import` compiles a tree into PL/SQL on the client and runs the
// blocks over its own JDBC connection. Over an ORDS connection that last step
// fails (`ORA-17004: Invalid column type`, the REST driver cannot carry the
// call), and APEX 26.2 has no server-side API that installs a tree. So this asks
// SQLcl's own transpiler for the blocks and prints them, and ADT sends them to
// ORDS itself, in one request behind the install settings they need.
//
// Run by `patch/apexlang_blocks.py` in Java's single-file source mode against the
// jars of the SQLcl ADT already uses, so the compiler is the one `validate` and
// `apex import` run.
//
//   java -cp <sqlcl>/lib/*:<sqlcl>/lib/ext/* ApexlangBlocks.java <tree> [key=value ...]
//
// Each key=value is a deployment value the compiler bakes into the blocks, the
// channel SQLcl's own `apex import -alias` reaches them through. The key is the
// property's `<component type id>:<property id>` (APEXSessionProperty's
// deployment map key, read off SQLcl 26.2): the alias is `1000:8`, and setting
// `APEX_APPLICATION_INSTALL.SET_APPLICATION_ALIAS` instead changes nothing,
// because the compiled `create_flow` carries the alias as a literal.
// Output: each block followed by one NUL character. A tree that does not
// compile prints SQLcl's own error text to stderr and exits 2.

import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Paths;
import java.util.LinkedHashMap;
import java.util.Map;

import oracle.apexlang.core.APEXLangCompilationResult;
import oracle.apexlang.core.DeploymentValue;
import oracle.dbtools.extension.apex.core.apexlang.APEXLangTranspiler;

public class ApexlangBlocks {
    public static void main(String[] args) throws Exception {
        Map<String, DeploymentValue> values = new LinkedHashMap<>();
        for (int i = 1; i < args.length; i++) {
            int split = args[i].indexOf('=');
            values.put(args[i].substring(0, split), new DeploymentValue(args[i].substring(split + 1)));
        }
        APEXLangTranspiler transpiler = new APEXLangTranspiler();
        APEXLangCompilationResult result = transpiler.transpile(Paths.get(args[0]), values);
        if (result.getErrors() != null && result.getErrors().length > 0) {
            System.err.println(transpiler.formatErrors(result.getErrors()));
            System.exit(2);
        }
        Writer out = new OutputStreamWriter(System.out, StandardCharsets.UTF_8);
        result.processPlSqlOutput(block -> {
            try {
                out.write(block);
                out.write('\0');
            } catch (java.io.IOException error) {
                throw new RuntimeException(error);
            }
        });
        out.flush();
    }
}
