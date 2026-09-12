#########################################################################################
#                                                                                       #
# Makefile pour PAPP (programme d'appariements)                                         #
# Makefile for PAPP (pairing program)                                                   #
# par/by Thierry Bousch, Emmanuel Lazard & Stephane Nicolet                             #
#                                                                                       #
# Makefile v1.37                                                                        #
# 28/08/2023 (Lazard) : Ajout de l'architecture arm pour MacOS                          #
# 29/11/2020 (Lazard) : English translation                                             #
# 17/07/2012 (Lazard) : Ajout des options 'pedantic' et 'ansi' pour se conformer au C90 #
#                     : 'pedantic' and 'ansi' options added to respect C90 standard     #
# 12/01/2007 (Lazard) : Ajout d'une option de compilation pour supprimer les warnings   #
#                     : compiler option added to suppress warnings                      #
#########################################################################################

# POUR COMPILER, IL FAUT LANCER LA CIBLE 'papp' EN TAPANT 'make papp'
# TO COMPILE, TARGET 'papp' MUST BE LAUNCHED BY TYPING 'make papp'

# Le compilateur utilise doit etre ANSI - Use an ANSI compiler
CC = gcc

# PAPP a ete compile au moins une fois sur des machines variees, et utilise les symboles suivants
#  exportes par les compilateurs: (Il ne faut donc pas les definir)
######
# PAPP was compile on various machines and uses the following symbols exported by compilers:
#  (They must therefore not be defined)
#
# 'atarist' pour l'Atari,
# '__GNUC__' pour le GCC,
# '__MSDOS__' pour MS-DOS,
# '__THINK_C__' pour le Think-C (Macintosh),
# '__MWERKS__' pour le CodeWarrior-C (Macintosh),
# '__BORLANDC__' pour Borland C++ pour Windows
# '__GO32__' pour le cross- compilateur MS-DOS (sous linux).
#
#-------------------------------------------------------------------------------
#
# On peut, par ailleurs, definir les symboles suivants soit pour faciliter la compilation
#  en fonction de l'OS, soit pour activer/desactiver certaines options
#  (voir aussi le fichier changes.h, qui definit d'autres symboles plus recents).
######
# The following symbols can also be defined either to help compilation
#  corresponding to the OS or to activate/desactivate some options
#  (see also changes.h file which defines other more recent symbols).
#
#----------- Symboles caracterisant le systeme d'exploitation ------------------
#----------- Symboles concerning Operating System             ------------------
#
# UNIX_BSD
#   si vous utilisez un Unix de type BSD, et - if you use a BSD type Unix and
#
# UNIX_SYSV
#   si vous utilisez un Unix de type Systeme 5. - if you use a System V Unix.
#
# MACOSX
#   si vous utilisez un Unix MacOS X - if you use a MacOS X Unix
#
# CYGWIN
#       pour la compilation sous Windows avec l'emulateur Unix cygwin
#       to compile under Windows OS with the cygwin Unix emulator
#
# LINUX
#      si vous utilisez un Unix Linux - if you use a Linux Unix
#
#----------- Symboles a definir ou non suivant votre OS ------------------------
#----------- Symboles to be defined or not depending on your OS ----------------
#
# TERMCAP
#   si vous avez la bibliotheque Termcap, sinon les commandes de gestion d'ecran (effacement, etc.)
#    devront etre codees en dur dans le programme. Unix surtout.
######
#   if you have the TERMCAP library, otherwise screen handling commands (clearing, etc.)
#    will have to be hard coded. Mostly Unix.
#
#
# NO_TERMCAP_H
#   si vous avez TERMCAP mais pas de fichier d'en-tete <termcap.h>.
######
#   if you do have TERMCAP but no header file <termcap.h>.
#
#
# POSIX_SIGNALS
#   si vous voulez utiliser les fonctions POSIX pour manipuler les
#    signaux (sigaction, sigprogmask, sigemptyset,...)
######
#   if you wish to use the POSIX functions to manipulate signals
#    (sigaction, sigprogmask, sigemptyset,...)
#
#
# NO_RAISE
#   si raise() n'est pas dans les bibliotheques -- c'est pourtant une fonction ANSI.
######
#   if raise() is not in the libraries -- it is however an ANSI function.
#
#
# P_OPEN
#   si votre compilateur connait les fonctions popen()/pclose() et si elles font
#    quelque chose de sense, evidemment. Si ce symbole est defini,
#    il est possible de "piper" le classement des joueurs vers une commande (less, par exemple).
######
#   if your compiler knows the popen()/pclose() functions and if they do something usefull.
#    if this symbol is defined, it is possible to pipe standings towards a command (less for example).
#
#
#----------- Options de compilation - Compilation options -----------
#
# ENGLISH
#   define this symbol if you want messages and menus in English.
#    The translations are not very good. Note that all key bindings  are the same as in the french version
#    (at least for now), except for the yes/no questions.
#    The config file must still be in French (well, not the comments, of course)
#    for compatibility with the french version.
#
#
# CURSOR (defaut/default: OFF)
#   sur certains terminaux (que nous ne nommerons pas), le curseur est invisible, ce qui est incommode
#    pour entrer du texte. Si ce symbole est defini, le curseur sera materialise par une tilde.
######
#   on certain terminals, cursor is invisible which inconvenient to enter some text.
#    If this symbol is defined, cursor will be displayed as a tilde.
#
#
# LOCK (disabled)
#   Lock files are disabled because PAPP can manage several saved tournaments
#    and let the user choose which tournament to resume at startup.
#
#
# DEBUG_PEN (defaut/default: OFF)
#   Si ce symbole est defini, PAPP affiche les penalites trouvees (ainsi que leur total)
#    dans les appariements optimaux.
######
#   If this symbol is defined, PAPP will display the penalties found (and their total)
#    in the final optimal pairings.
#
#
# SAVE_MATRIX (defaut/default: OFF)
#   sauve la matrice des penalites dans 'matrix.pen' avant de lancer l'algorithme d'Edmonds,
#    sous une forme ou elle peut etre relue par le programme '''test'.
#    Peut etre utile pour la mise au point du module edmonds.c.
#    Les resultats de l'optimisation (si on arrive jusque la) seront stockes dans 'matrix.out'.
######
#   Save the penalties matrix in 'matrix.pen', before starting the Edmonds algorithm,
#    so that it can be read by the 'test' program.
#    This can be useful when tuning the edmonds.c module.
#    Optimisation results will be saved in 'matrix.out'
#
#
# USE_LONG_LONG (defaut/default: OFF)
#   si vous voulez utiliser des "long long" dans edmonds.c (entiers sur 64 bits)
#    au lieu des "double" (flottants sur 64 bits). Seul le GCC supporte ce type.
#    Peut etre necessaire si la bibliotheque flottante est boguee ou imprecise.
#    (Ce symbole est automatiquement defini si __GO32__ est defini).
######
#   If you want to use "long long" in edmonds.c (64 bits int) instead of "double"
#    (64 bits floating point numbers). Only GCC supports this type.
#    May be necessary if the float library is bugged or imprecise
#    (this symbol is automatically defined if  __GO32__ is defined).
#
#
# EDEBUG (defaut/default: OFF)
#   affiche enormement de details sur le deroulement de l'algorithme d'Edmonds.
#    N'est utile que pour la mise au point.
######
#   Displays at lot of details during the Edmonds algorithm flow.
#    only useful during the tunign of the algorithm
#
#
# MIXING (defaut/default: ON)
#   si vous voulez melanger les joueurs avant toute optimisation, c.a.d. pas uniquement
#    a la premiere ronde. Ceci a pour consequence de rendre les appariements non deterministes.
######
#   If you want to mix players before any optimisation, that is not only before round 1.
#    As a consequence, pairings will not be deterministic.
#
#
# EIGHT_BITS (defaut/default: OFF)
#   si vous voulez autoriser l'utilisateur a entrer des caracteres huit bits au clavier.
#    A utiliser avec circonspection, parce que cela diminue la portabilite vers d'autres machines.
#    Seules les machines utilisant le jeu ISO-Latin-1 (windows, unix) devraient avoir
#    le droit de definir ce symbole.
######
#   If you wish to authorize user to enter 8-bits characters with the keyboard.
#    Use with caution because portability to other machines may be reduced.
#    Only machines using ISO-Latin-1 set (Windows, Linux) should hav the right
#    to define ths symbol.
#
#
#########################################################################################
#
# Voici les options rajoutees a la compilation - Here are the added compiler options
#
EXTRA  = -DMIXING -DINCLUDE_COUNTRY
#
#-------------------------------------------------------------------------------
#
# Voici les commmandes de compilation pour divers compilateurs
#  Il faut toutes les commenter sauf celle qui vous correspond.
######
# Here are all compilation commands for various compilers
#  They must all be commented except the one which corresponds to your system.
#
#------------------------------------
#

COMMON_FLAGS = -D_DEFAULT_SOURCE -D_POSIX_C_SOURCE=200809L
COMMON_OPTIONS = -O2 -pipe
COMMON_WARNINGS = -Wall -Wextra -pedantic -ansi \
	-Wstrict-prototypes -Wno-unused-function -Wimplicit-function-declaration \
	-Wno-char-subscripts -Wno-unused-but-set-variable

ifeq ($(OS),Windows_NT)
	ifeq ($(MINGW_NATIVE),1)
		# Native MinGW/UCRT64 Windows build (no Cygwin or ncurses DLL required)
		CFLAGS = $(COMMON_OPTIONS) $(COMMON_FLAGS) $(COMMON_WARNINGS) $(EXTRA) \
			-DPAPP_MINGW -Wno-unused-but-set-variable \
			-Wimplicit-fallthrough=0 -Wno-format-overflow \
			-Wno-stringop-truncation -Wno-stringop-overflow
		LDFLAGS =
		LIBS = -lm
		EXE_SUFFIX = .exe
	else
		# CygWin sous Windows avec ncurses - Cygwin under Windows with ncurses
		CFLAGS = $(COMMON_OPTIONS) $(COMMON_FLAGS) $(COMMON_WARNINGS) $(EXTRA) \
			-DCYGWIN -DUNIX_BSD -DPOSIX_SIGNALS \
			-Wno-unused-but-set-variable -Wimplicit-fallthrough=0 \
			-Wno-format-overflow -Wno-stringop-truncation -Wno-stringop-overflow
		LDFLAGS =
		LIBS = -lm -lncurses
		EXE_SUFFIX =
	endif
	ARCH = Windows
else
	UNAME_S := $(shell uname -s)
	ifeq ($(UNAME_S),Darwin)
		# MacOS X
		CFLAGS = $(COMMON_OPTIONS) $(COMMON_FLAGS) $(COMMON_WARNINGS) $(EXTRA) \
			-DMACOSX -DUNIX_BSD -DPOSIX_SIGNALS -DP_OPEN -DTERMCAP -DTERMCAP_H \
	    	-arch arm64 -arch x86_64 -mmacosx-version-min=10.10
		LDFLAGS = -mmacosx-version-min=10.10
		LIBS = -lm -ltermcap -arch arm64 -arch x86_64
		ARCH = MacOS
	else
		ifeq ($(UNAME_S),Linux)
			# Linux with Termcap library
			# (if you don't have it at the right place, copy it into the papp directory)
			CFLAGS = $(COMMON_OPTIONS) $(COMMON_FLAGS) $(COMMON_WARNINGS) $(EXTRA) \
				-DLINUX -DUNIX_SYSV -DPOSIX_SIGNALS -DP_OPEN -DTERMCAP -DTERMCAP_H \
	    	    -Wno-unused-but-set-variable -Wimplicit-fallthrough=0 \
  	    		-Wno-format-overflow -Wno-stringop-truncation -Wno-stringop-overflow \
	    	    -Wmisleading-indentation -fomit-frame-pointer
			LDFLAGS = -L.
			LIBS = -lm -ltermcap
		ARCH = Linux
		endif
	endif
	EXE_SUFFIX =
endif

#----------

# CygWin sous Windows avec ncurses - Cygwin under Windows with ncurses

#CFLAGS = -DCYGWIN -DUNIX_BSD -D_POSIX_C_SOURCE=200809L \
#         -DPOSIX_SIGNALS \
#         $(EXTRA) \
#         -O2 -pipe \
#         -Wstrict-prototypes -Wall -Wextra -Wno-unused-function \
#         -Wno-char-subscripts -pedantic -ansi
#LDFLAGS =
#LIBS = -lm -lncurses

#----------

# Linux without Termcap library

#CFLAGS = -DLINUX -DUNIX_SYSV -D_POSIX_C_SOURCE=200809L  \
#         -DPOSIX_SIGNALS -DP_OPEN -DNO_TERMCAP_H \
#         $(EXTRA) \
#         -O2 -pipe \
#         -Wstrict-prototypes -Wall -Wextra -Wno-unused-function \
#         -fomit-frame-pointer
#LDFLAGS =
#LIBS = -lm

#----------

#  SunOS (or other BSD Unix versions) with Termcap library
#
#CFLAGS = -DUNIX_BSD \
#         -DPOSIX_SIGNALS -DP_OPEN -DTERMCAP -DTERMCAP_H -DNO_RAISE \
#         $(EXTRA) \
#         -O2 -pipe \
#         -Wstrict-prototypes -Wall -Wextra -Wno-unused-function \
#LDFLAGS =
#LIBS = -lm -ltermcap

#----------

#  SunOS (or other BSD Unix versions) without Termcap library
#
#CFLAGS = -DUNIX_BSD \
#         -DPOSIX_SIGNALS -DP_OPEN -DNO_TERMCAP_H -DNO_RAISE \
#         $(EXTRA) \
#         -O2 -pipe \
#         -Wstrict-prototypes -Wall -Wextra -Wno-unused-function \
#LDFLAGS =
#LIBS = -lm

#----------

# Ms-Dos cross-compilation
#
#GO32_CFLAGS = \
#       $(EXTRA) \
#       -O2 -fomit-frame-pointer -pipe -m486 -Wall
#GO32_LDFLAGS = -s
#GO32_LIBS = -lm -lpc
#GO32_CC = go32gcc
#GO32_STUB = /usr/local/go32/lib/stub.exe

# Compilation options for Windows:
#
# When compiling with the Borland C++ compiler, you should set Large model
# in the Code Generation dialog box. Then create a project with the C files specified below
# (after the CFILE keyword), then Build All, and Run.

# Following rules enable checking the generated assembler code and cross-compile for Ms-Dos.
# You may comment them if there is an issue with your 'make' version.
#
# %.s: %.c
# 	$(CC) $(CFLAGS) -S $< -o $@
# %.oms: %.c
# 	$(GO32_CC) $(GO32_CFLAGS) -c $< -o $@
# %.sms: %.c
# 	$(GO32_CC) $(GO32_CFLAGS) -S $< -o $@

#-------------------------------------------------------------------------------------
#
# Voici la liste de tous les fichiers sources. Ces fichiers peuvent etre sauvegardes avec 'make archive'
# Here is the list of all source files. They can be saved with 'make archive'
#
HEADERS = changes.h couplage.h crosstable.h pairings.h discs.h global.h \
          more.h pairings.h player.h tiebreak.h version.h

CFILES  = couplage.c crosstable.c discs.c edmonds.c elo.c \
    enterPlayer.c global.c main.c more.c penalties.c player.c \
    results.c roundrobin.c tiebreak.c XML.c

SFILES  = Makefile pap.l pap.y
DOCUS   = papp_doc.tex Bugs A_faire Changes
AUTRES  = pap_tab.h pap_tab.c lexyy.c joueurs

RCSFILES = $(HEADERS) $(CFILES) $(SFILES) $(DOCUS)
SOURCES = $(RCSFILES) $(AUTRES)

#
# Fichiers objets requis par PAPP - Object files required by PAPP
#

OBJECTS  = \
    lexyy.o pap_tab.o \
    couplage.o crosstable.o tiebreak.o edmonds.o elo.o \
    enterPlayer.o global.o player.o main.o more.o  penalties.o \
    discs.o results.o roundrobin.o XML.o tournament_json.o

OBJECTS_DOS = \
    lexyy.oms pap_tab.oms \
    edmonds.oms global.oms player.oms main.oms elo.oms more.oms \
    enterPlayer.oms roundrobin.oms couplage.oms results.oms penalties.oms \
    discs.oms crosstable.oms tiebreak.oms XML.oms

#
# Les programmes 'exemple' et 'geom' produisent des matrices d'exemples pour le programme 'testp'.
#  Le programme proprement dit est 'papp', sa documentation est 'papp_doc.tex',
#  et 'changes' contient l'historique des versions.
######
# The 'exemple' and 'geom' programs produce exemple matrices for the 'testp' program.
#  The main program is 'papp', its documentation is 'papp_doc.tex',
#  and 'changes' contains versions history.
#

ifeq ($(MINGW_NATIVE),1)
all: papp_GB
else
all:
	@echo "\nBuilding for" $(ARCH)
	$(MAKE) papp
	@echo "\nBuilding English version for" $(ARCH)
	$(MAKE) papp_GB
endif

papp : LANGUAGE=
papp : $(OBJECTS)
	$(CC) $(LDFLAGS) -o papp$(EXE_SUFFIX) $(OBJECTS) $(LIBS)
	rm -f *.o
	mv papp$(EXE_SUFFIX) ../bin/$(ARCH)

papp_GB : LANGUAGE=-DENGLISH
papp_GB : $(OBJECTS)
	$(CC) $(LDFLAGS) -o papp_GB$(EXE_SUFFIX) $(OBJECTS) $(LIBS)
	rm -f *.o
	mv papp_GB$(EXE_SUFFIX) ../bin/$(ARCH)

%.o : %.c
	$(CC) -c $< $(CFLAGS) $(LANGUAGE)

.PHONY : clean
clean:
	rm -f core *.s *.sms *.o *.oms *.bak *.go32
	rm -f papp_doc.log papp_doc.aux papp_doc.toc

.PHONY : veryclean
veryclean: clean
	rm -f *.exe $(BINAIRES) lexyy.c pap_tab.c pap_tab.h
	rm -f papp_doc.dvi papp_doc.ps
	rm -f class_*.txt ronde_*.txt result_*.txt resul_*.txt
	rm -f team_*.txt crosstable_*.htm crosstable_*.html

#
# Ces targets ne sont pas utilises sauf en test - Unused targets unless testing

BINAIRES_TEST = testp geom exemple edtest contest papp

msdos: edtest.exe papp.exe

testp: discs.o edmonds.o test.o
	$(CC) $(LDFLAGS) -o testp $^ $(LIBS)

edtest: discs.o edmonds.o edtest.o
	$(CC) $(LDFLAGS) -o edtest $^ $(LIBS)

contest: discs.o edmonds.o contest.o
	$(CC) $(LDFLAGS) -o contest $^ $(LIBS)

papp.exe: $(OBJECTS_DOS)
	$(GO32_CC) $(GO32_LDFLAGS) -o papp.go32 $(OBJECTS_DOS) $(GO32_LIBS)
	cat $(GO32_STUB) papp.go32 >papp.exe

edtest.exe: discs.oms edmonds.oms edtest.oms
	$(GO32_CC) $(GO32_LDFLAGS) -o edtest.go32 $^ $(GO32_LIBS)
	cat $(GO32_STUB) edtest.go32 >edtest.exe

exemple: exemple.c
	$(CC) $(CFLAGS) $(LDFLAGS) exemple.c -o exemple $(LIBS)

geom: geom.c
	$(CC) $(CFLAGS) $(LDFLAGS) geom.c -o geom $(LIBS)


#
# Archivage - Archives
#
archive: appari.tgz
	@echo Sauvegarde du dossier RCS
	tar -czf $$HOME/papp_rcs.tgz RCS

appari.tgz: $(SOURCES)
	tar -czf appari.tgz $(SOURCES)

distrib: appari.tgz
	./makedist

#
# Maintenant que la documentation est en LaTeX,
#  nous ajoutons une 'cible' pour previsualiser le document
######
# Now that documentation is in LaTeX format, a target is added to preview document.
#

preview: papp_doc.dvi
	xdvi papp_doc.dvi &

papp_doc.dvi: papp_doc.tex
	latex papp_doc.tex
	latex papp_doc.tex


#
# Dependances pour Lex et Yacc. - Lex and yacc dependencies
#
lexyy.c: pap.l
	flex -8 pap.l
	mv lex.yy.c lexyy.c

lexyy.o: pap_tab.h

pap_tab.c: pap_tab.h
	touch pap_tab.c

pap_tab.h: pap.y
	@echo "pap.y should have 2 shift/reduce type conflict"
	bison -d pap.y
	mv pap.tab.c pap_tab.c
	mv pap.tab.h pap_tab.h

#
# Autres dependances (on n'est jamais trop prudent) - Other dependencies (never cautious enough)
#
$(OBJECTS) $(OBJECTS_DOS): $(HEADERS)
test.o edtest.o contest.o: pairings.h discs.h $(HEADERS)
test.oms edtest.oms contest.oms: pairings.h discs.h $(HEADERS)
