/** Lists primary source files actually extracted from compiler invocations. */
import cpp

string termination(Compilation compilation) {
  compilation.normalTermination() and result = "normal"
  or
  not compilation.normalTermination() and result = "abnormal"
}

from Compilation compilation, File file
where file = compilation.getAFileCompiled()
select file.getAbsolutePath() as path,
  termination(compilation) as termination
