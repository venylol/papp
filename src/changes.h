/* change.h
 *
 *  - definitions diverses et variables d'environnement pour chaque compilateur.
 *    Voir aussi le "makefile".
 *  - various definitions for each compiler. See also the "makefile" file
 *    for operating systems/compilers supporting makefiles.
 *
 * (EL) 15/06/2018 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change.
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33, no change
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 13/01/2007 : v1.30 E. Lazard, no change
 * (SN) 01/04/2001 : v1.29 Stephane Nicolet
 */


/*
 * The following symbols allow conditional compilation
 * of the new features introduced in PAPP since v1.17
 */

#ifndef OPTIONS_GLOBALES_PAPP
#define OPTIONS_GLOBALES_PAPP

    /* Lock files are disabled.  PAPP manages separate tournament workspaces
       instead of preventing a second process from starting. */
    #undef LOCK

    #define TABLES_NUMBER
    #define DISPLAY_ALPHA_COUPLING
    #define COMPENSATION_FLOATS
    #define ELITISM
    #define USE_HALF_DISCS

#endif  /*   #ifndef OPTIONS_GLOBALES_PAPP ...   */

/*
 * The following blocks allow to make
 * specific compilers changes
 */

#ifndef COMPILER_OPTIONS
#define COMPILER_OPTIONS

/* -- Codewarrior 9 compiler for Macintosh -- */
    #if defined(__MWERKS__) && defined(macintosh)

       #include "SIOUX.H"
       #include "SIOUXMenus.h"
       #include "SIOUXGlobals.h"
       #include <Printing.h>
       #include <stdio.h>
       #include <ctype.h>
       #include <unix.h>
       #include <OSUtils.h>
       #include <CursorDevices.h>


       #define PAPP_MAC_METROWERKS    __MWERKS__
       /* debuguer les penalites */
       #define DEBUG_PEN           1
       /* mais pas le reste */
       #undef  EDEBUG
       /* ecrire sur disque la matrice utilisee par edmonds.c */
       #undef SAVE_MATRIX

       /* on utilise un curseur en video inverse */
       #define CURSOR

       /* Semble manquer dans CodeWarrior */
       #define ENOENT (-43)   /* -43 = FileNotFoundErr */
       /* On redefinit goodbye et beep pour eviter des conflits avec des librairies obscures */
       #define goodbye Papp_goodbye
       #define beep Papp_beep

       /* On redefinit assert pour avoir le temps de lire les messages d'erreur */
       #define USE_PAPP_ASSERT
       #ifdef USE_PAPP_ASSERT
          #undef assert
          #define assert  papp_assert

          #define papp_assert(test) \
                  ((test) ? (void)0 : ( fatal_error("\nassert failed: " __FILE__ ":" _STR(__LINE__) " " #test) , (void)0))
       #endif

       /* CodeWarrior ne connait pas alloca */
       #define alloca malloc

       /* no need do duplicate the files before parsing them on Macintosh */
       #undef  DUPLIQUE_FICHIER_PARSER

    #endif


/* -- THINK C compiler for Macintosh OS 9-- */
    #ifdef __THINK_C__

       #define DEBUG_PEN           1

       /* on n'utilise pas ~ comme curseur */
       #undef CURSOR

       /* THINK C ne connait pas alloca */
       #define alloca malloc

       /* On redefinit assert pour avoir le temps de lire les messages d'erreur */
       #define USE_PAPP_ASSERT
       #ifdef USE_PAPP_ASSERT
          #undef assert
          #define assert  papp_assert

          #define papp_assert(test) \
                  ((test) ? (void)0 : ( fatal_error("\nassert failed: " __FILE__ ":" _STR(__LINE__) " " #test) , (void)0))
       #endif

       /* no need do duplicate the files before parsing them on Macintosh */
       #undef  DUPLIQUE_FICHIER_PARSER

    #endif

/* -- Codewarrior 9 compiler for Windows -- */
    #if defined(__MWERKS__) && defined(__INTEL__)

       #define PAPP_WINDOWS_METROWERKS    1
       /* debuguer les penalites */
       #define DEBUG_PEN           1
       /* mais pas le reste */
       #undef  EDEBUG

       /* on utilise un curseur en video inverse */
       #define CURSOR


       #include <stdio.h>
       #include <ctype.h>
       #include <io.h>
       #include <conio.h>
       #include <WinSIOUX.h>

       /* on definit les synonymes usuels*/
       #define isatty   _isatty
       #define fileno   _fileno

       /* On redefinit assert pour avoir le temps de lire les messages d'erreur */
       #undef USE_PAPP_ASSERT
       #ifdef USE_PAPP_ASSERT
          #undef assert
          #define assert  papp_assert

          #define papp_assert(test) \
                  ((test) ? (void)0 : ( fatal_error("\nassert failed: " __FILE__ ":" _STR(__LINE__) " " #test) , (void)0))
       #endif

       /* CodeWarrior ne connait pas alloca */
       #define alloca malloc

       /* no need do duplicate the files before parsing them on Macintosh */
       #define  DUPLIQUE_FICHIER_PARSER

    #endif

/* -- Borland C++ compiler for Windows. Jakub Tesinsky               */
/* -- Beware: compile with 'far' memory model. */
    #ifdef   __BORLANDC__

        /* we have to duplicate the files before parsing them, */
        /* because of some limitation in PC Flex.              */
        #define DUPLIQUE_FICHIER_PARSER

    #endif

/* -- Native MinGW Windows compiling -- */
/* The original Windows target above is for Cygwin.  MinGW supplies the
 * standard C library and Windows console directly, so it needs a small set
 * of names compatible with the portable PAPP sources. */
    #ifdef PAPP_MINGW

        #include <conio.h>
        #include <direct.h>
        #include <io.h>

        #define mkdir(path, mode) _mkdir(path)
        #define isatty             _isatty
        #define fileno             _fileno
        #define srandom             srand
        #define random              rand

    #endif

/* -- Ms-Dos compiling -- */
    #if defined(__MSDOS__)

        /* maybe we need do duplicate the files before parsing    */
        /* them on MS-Dos, because of some limitation in PC Flex. */
        /* if so, change the following #undef to #define          */
        #undef DUPLIQUE_FICHIER_PARSER

    #endif

/* -- Unix compiling -- */
/* -- See also "makefile" -- */
    #if defined(UNIX_SYSV) || defined(UNIX_BSD)

        /* no need to duplicate the files before parsing them on unix */
        #undef DUPLIQUE_FICHIER_PARSER

    #endif

#endif  /*   #ifndef COMPILER_OPTIONS ...   */
