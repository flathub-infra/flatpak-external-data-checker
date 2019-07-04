// Load endless library from master branch to local namespace
@Library('endless@master') _

node('flatpak-builder') {
    try {
        def image

        // Global env variables used by git
        env.GIT_AUTHOR_NAME='Endless External Data Checker'
        env.GIT_COMMITTER_NAME='Endless External Data Checker'
        env.GIT_AUTHOR_EMAIL='desktop@endlessm.com'
        env.GIT_COMMITTER_EMAIL='desktop@endlessm.com'

        stage('Clone repository') {
            checkout scm
        }

        stage('Build container') {
            /* FIXME: We pass the user and group ids when building the image as
             * a workaround to make sure the user running the container exists
             * inside of it (see Dockerfile). This is needed because jenkins runs
             * the container with the same user as the host (with '-u <uid>:<gid>')
             * but 'git push' fails with 'No user exists for uid ...' if the user
             * doesn't exist inside the container (other option is to mount
             * '/etc/passwd' as a volume and export HOME/USER envs accordingly
             * when running the container ('image.inside' below)).
             *
             * See also https://issues.jenkins-ci.org/browse/JENKINS-47026.
             */
            image = docker.build('flatpak-external-data-checker:latest',
                                 '--build-arg USER_ID=$(id -u) --build-arg GROUP_ID=$(id -g) .')
        }

        stage('Run checker in container') {
            try {
                /* The '--privileged' param is required to run 'bwrap' which is used
                 * by the flatpak external data checker */
                image.inside('--privileged') {
                    sshagent(credentials: [ 'fe45ca53-7c92-47db-b3b1-b8d0cc8507ed' ]) {
                        withCredentials([string(credentialsId: 'github-api-token-rw-jobs', variable: 'GITHUB_TOKEN')]) {
                            sh '''
                                export GIT_SSH_COMMAND=\'ssh -oStrictHostKeyChecking=no\'
                                ./wrappers/jenkins-check-flatpak-external-apps \
                                    --ext-data-checker=./src/flatpak-external-data-checker \
                                    --update \
                                    --verbose
                            '''
                        }
                    }
                }
            } finally {
                // Remove workspace when done
                deleteDir()
            }
        }
    } catch (e) {
        endless.notifyFailed('endless-dev-status@endlessm.com, wjt@endlessm.com')
        throw e
    }
}
